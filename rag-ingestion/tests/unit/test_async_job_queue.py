
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
from app.api.v1.routers.curriculum import generate_curriculum, get_job_status, GenerateCurriculumRequest, JobStatus
from app.workers.curriculum_worker import process_job, worker_loop
import asyncio

class TestAsyncJobQueue(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Mock Supabase
        self.mock_supabase = MagicMock()
        self.mock_table = MagicMock()
        self.mock_rpc = MagicMock()
        
        self.mock_supabase.table.return_value = self.mock_table
        self.mock_supabase.rpc.return_value = self.mock_rpc
        
        # Patch client getter in Router
        self.router_patcher = patch("app.api.v1.routers.curriculum.get_async_supabase_client", return_value=self.mock_supabase)
        self.mock_get_client_router = self.router_patcher.start()
        
        # Patch client getter in Worker
        self.worker_patcher = patch("app.workers.curriculum_worker.get_async_supabase_client", return_value=self.mock_supabase)
        self.mock_get_client_worker = self.worker_patcher.start()

        # Patch Graph Invoke
        self.graph_patcher = patch("app.workers.curriculum_worker.curriculum_graph")
        self.mock_graph = self.graph_patcher.start()

    async def asyncTearDown(self):
        self.router_patcher.stop()
        self.worker_patcher.stop()
        self.graph_patcher.stop()

    async def test_api_enqueue(self):
        # Setup
        request = GenerateCurriculumRequest(
            topic="Test Topic",
            course_level="Beginner",
            source_document_id="doc_123",
            tenant_id="tenant_123"
        )
        
        # Mock Insert Response
        mock_response = MagicMock()
        mock_response.data = [{"id": "job_123", "status": "pending"}]
        self.mock_table.insert.return_value.execute = AsyncMock(return_value=mock_response)
        
        # Execute
        response = await generate_curriculum(request)
        
        # Verify
        self.assertEqual(response.job_id, "job_123")
        self.assertEqual(response.status, JobStatus.PENDING)
        
        # Verify call
        self.mock_table.insert.assert_called_once()
        args, _ = self.mock_table.insert.call_args
        self.assertEqual(args[0]["job_type"], "structured_synthesis_generation")
        self.assertEqual(args[0]["payload"]["topic"], "Test Topic")

    async def test_worker_processing_success(self):
        # Setup Job
        job = {
            "id": "job_123",
            "job_type": "curriculum_generation",
            "payload": {
                "topic": "Test Topic",
                "course_level": "Beginner",
                "source_document_id": "doc_123"
            },
            "tenant_id": "tenant_123"
        }
        
        # Mock Graph Result
        self.mock_graph.invoke = AsyncMock(return_value={"selected_concepts": [{"title": "C1"}]})
        
        # Mock Update Response
        mock_update_response = MagicMock()
        mock_update_response.data = [{"id": "job_123", "status": "completed"}]
        self.mock_table.update.return_value.eq.return_value.execute = AsyncMock(return_value=mock_update_response)
        
        # Execute
        await process_job(job)
        
        # Verify Graph Called
        self.mock_graph.invoke.assert_called_once()
        
        # Verify DB Update
        self.mock_table.update.assert_called_once()
        args, _ = self.mock_table.update.call_args
        self.assertEqual(args[0]["status"], "completed")
        self.assertEqual(args[0]["result"]["concepts"][0]["title"], "C1")

    async def test_worker_processing_failure(self):
        # Setup Job
        job = {
            "id": "job_123",
            "job_type": "curriculum_generation",
            "payload": {
                "topic": "Bad Topic",
                "course_level": "Beginner",
                "source_document_id": "doc_123"
            },
            "tenant_id": "t1"
        }
        
        # Mock Graph Failure
        self.mock_graph.invoke = AsyncMock(side_effect=Exception("Graph Crash"))
        
        # Mock Update Response
        self.mock_table.update.return_value.eq.return_value.execute = AsyncMock()
        
        # Execute
        await process_job(job)
        
        # Verify DB Update for Failure
        self.mock_table.update.assert_called_once()
        args, _ = self.mock_table.update.call_args
        self.assertEqual(args[0]["status"], "failed")
        self.assertIn("Graph Crash", args[0]["error_message"])

if __name__ == "__main__":
    unittest.main()
