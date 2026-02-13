import structlog
import time
from typing import Any, Dict, List, Optional, Union
from uuid import UUID
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_core.messages import BaseMessage
from app.core.observability.correlation import get_correlation_id

logger = structlog.get_logger("agent_trace")

class AgentTelemetryCallback(BaseCallbackHandler):
    """
    Callback Handler for LangGraph/MAS Orchestration Tracing.
    Provides visibility into node execution, tool usage, and state transitions.
    """
    
    def __init__(self):
        self.node_start_times = {}
        self.tool_start_times = {}
        self.last_states = {}

    def on_chain_start(
        self, serialized: Dict[str, Any], inputs: Dict[str, Any], **kwargs: Any
    ) -> Any:
        try:
            name = serialized.get("name") or kwargs.get("name") or "unknown_node"
            run_id = str(kwargs.get("run_id"))
            
            # Filter LangGraph noise: only log actual nodes
            if "LangGraph" in name or name == "StateGraph":
                return
                
            self.node_start_times[run_id] = time.time()
            
            logger.info(
                f"Node Execution Started: {name}",
                type="agent_trace",
                event="node_start",
                node_id=name,
                correlation_id=get_correlation_id()
            )
        except Exception as e:
            # Fail-safe: don't break the agent if telemetry fails
            pass

    def on_chain_end(self, outputs: Dict[str, Any], **kwargs: Any) -> Any:
        try:
            name = kwargs.get("name") or "unknown_node"
            run_id = str(kwargs.get("run_id"))
            
            if "LangGraph" in name or name == "StateGraph":
                return
                
            start_time = self.node_start_times.pop(run_id, time.time())
            duration_ms = int((time.time() - start_time) * 1000)
            
            # State Diffing (Concept)
            # Since on_chain_end only sees the output of the node, 
            # we log the keys that were modified in this step.
            changed_keys = list(outputs.keys()) if isinstance(outputs, dict) else []
            
            logger.info(
                f"Node Execution Completed: {name}",
                type="agent_trace",
                event="node_end",
                node_id=name,
                duration_ms=duration_ms,
                changed_keys=changed_keys,
                correlation_id=get_correlation_id()
            )
        except Exception as e:
            pass

    def on_tool_start(
        self, serialized: Dict[str, Any], input_str: str, **kwargs: Any
    ) -> Any:
        try:
            tool_name = serialized.get("name") or "unknown_tool"
            run_id = str(kwargs.get("run_id"))
            parent_node = kwargs.get("parent_run_id") # Can be used to link to node_id
            
            self.tool_start_times[run_id] = time.time()
            
            logger.info(
                f"Tool Usage Started: {tool_name}",
                type="agent_trace",
                event="tool_start",
                agent_action={
                    "tool": tool_name,
                    "input": input_str
                },
                correlation_id=get_correlation_id()
            )
        except Exception as e:
            pass

    def on_tool_end(self, output: str, **kwargs: Any) -> Any:
        try:
            run_id = str(kwargs.get("run_id"))
            start_time = self.tool_start_times.pop(run_id, time.time())
            duration_ms = int((time.time() - start_time) * 1000)
            
            # Security: Truncate large tool outputs
            safe_output = str(output)[:500] + "..." if len(str(output)) > 500 else str(output)
            
            logger.info(
                f"Tool Usage Completed",
                type="agent_trace",
                event="tool_end",
                duration_ms=duration_ms,
                tool_output=safe_output,
                correlation_id=get_correlation_id()
            )
        except Exception as e:
            pass

    def on_llm_start(
        self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any
    ) -> Any:
        # Phase 3 already covers model detail tracing, but we can log that a node called an LLM here
        pass

    def on_error(self, error: Union[Exception, KeyboardInterrupt], **kwargs: Any) -> Any:
        try:
            logger.error(
                "Agent Trace Error",
                type="agent_trace",
                event="error",
                error=str(error),
                node_id=kwargs.get("name"),
                correlation_id=get_correlation_id()
            )
        except Exception:
            pass
