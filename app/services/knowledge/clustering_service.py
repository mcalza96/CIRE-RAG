import logging
import numpy as np
from typing import List, Dict
from uuid import UUID
from sklearn.mixture import GaussianMixture
import asyncio
import networkx as nx
from app.ai.llm import get_llm
from app.infrastructure.supabase.client import get_async_supabase_client
from app.services.embedding_service import JinaEmbeddingService
from app.domain.raptor_schemas import ClusterResult, ClusterAssignment

logger = logging.getLogger(__name__)

try:
    import igraph as ig
    import leidenalg
except ImportError:  # pragma: no cover - dependency guard
    ig = None
    leidenalg = None

class GMMClusteringService:
    """
    Gaussian Mixture Model clustering for semantic grouping of chunks.
    Supports soft clustering where a chunk can belong to multiple clusters.
    """
    
    def __init__(
        self, 
        min_clusters: int = 2,
        max_clusters: int = 10,
        soft_threshold: float = 0.3,
        random_state: int = 42
    ):
        self.min_clusters = min_clusters
        self.max_clusters = max_clusters
        self.soft_threshold = soft_threshold
        self.random_state = random_state
    
    def _determine_optimal_clusters(self, embeddings: np.ndarray) -> int:
        """
        Determine optimal number of clusters using BIC minimization.
        """
        n_samples = len(embeddings)
        
        # Can't have more clusters than samples
        max_k = min(self.max_clusters, n_samples - 1)
        min_k = min(self.min_clusters, max_k)
        
        if max_k <= min_k:
            return min_k
        
        best_bic = float('inf')
        best_n = min_k
        
        for n_components in range(min_k, max_k + 1):
            try:
                gmm = GaussianMixture(
                    n_components=n_components,
                    random_state=self.random_state,
                    covariance_type='diag',
                    max_iter=100
                )
                gmm.fit(embeddings)
                bic = gmm.bic(embeddings)
                
                if bic < best_bic:
                    best_bic = bic
                    best_n = n_components
                    
            except Exception as e:
                logger.warning(f"GMM failed for n={n_components}: {e}")
                continue
        
        logger.info(f"Optimal cluster count: {best_n} (BIC: {best_bic:.2f})")
        return best_n
    
    def cluster(
        self, 
        chunk_ids: List[UUID], 
        embeddings: np.ndarray
    ) -> ClusterResult:
        """
        Cluster embeddings using GMM with soft assignment.
        """
        n_samples = len(chunk_ids)
        
        if n_samples < 2:
            return ClusterResult(
                num_clusters=1,
                assignments=[ClusterAssignment(
                    chunk_id=chunk_ids[0],
                    cluster_id=0,
                    probability=1.0
                )],
                cluster_contents={0: chunk_ids}
            )
        
        n_clusters = self._determine_optimal_clusters(embeddings)
        
        gmm = GaussianMixture(
            n_components=n_clusters,
            random_state=self.random_state,
            covariance_type='diag',
            max_iter=100
        )
        gmm.fit(embeddings)
        
        proba = gmm.predict_proba(embeddings)
        
        assignments: List[ClusterAssignment] = []
        cluster_contents: Dict[int, List[UUID]] = {i: [] for i in range(n_clusters)}
        
        for i, chunk_id in enumerate(chunk_ids):
            for cluster_id in range(n_clusters):
                prob = proba[i, cluster_id]
                if prob >= self.soft_threshold:
                    assignments.append(ClusterAssignment(
                        chunk_id=chunk_id,
                        cluster_id=cluster_id,
                        probability=float(prob)
                    ))
                    cluster_contents[cluster_id].append(chunk_id)
        
        cluster_contents = {k: v for k, v in cluster_contents.items() if v}
        logger.info(f"GMM clustering: {n_samples} chunks -> {len(cluster_contents)} active clusters")
        
        return ClusterResult(
            num_clusters=len(cluster_contents),
            assignments=assignments,
            cluster_contents=cluster_contents
        )


class ClusteringService:
    """
    Offline community detection + summarization for GraphRAG.
    Uses NetworkX for graph assembly and Leiden (igraph/leidenalg) for partitioning.
    """

    def __init__(
        self,
        supabase_client=None,
        embedding_service: JinaEmbeddingService | None = None,
        resolution: float = 1.0,
        max_entities_for_prompt: int = 60,
    ):
        self._supabase = supabase_client
        self._embedding_service = embedding_service or JinaEmbeddingService.get_instance()
        self._llm = get_llm(temperature=0.2, capability="CHAT")
        self._resolution = resolution
        self._max_entities_for_prompt = max_entities_for_prompt

    async def _get_client(self):
        if self._supabase is None:
            self._supabase = await get_async_supabase_client()
        return self._supabase

    async def _fetch_tenant_graph_data(self, tenant_id: UUID) -> tuple[list[dict], list[dict]]:
        client = await self._get_client()
        tenant_str = str(tenant_id)

        entities_resp = await client.table("knowledge_entities").select(
            "id,name,description"
        ).eq("tenant_id", tenant_str).execute()

        relations_resp = await client.table("knowledge_relations").select(
            "source_entity_id,target_entity_id,weight"
        ).eq("tenant_id", tenant_str).execute()

        entities = entities_resp.data or []
        relations = relations_resp.data or []
        return entities, relations

    @staticmethod
    def _build_networkx_graph(entities: list[dict], relations: list[dict]) -> nx.Graph:
        graph = nx.Graph()

        for entity in entities:
            entity_id = str(entity.get("id"))
            if not entity_id:
                continue
            graph.add_node(entity_id)

        for relation in relations:
            source = str(relation.get("source_entity_id"))
            target = str(relation.get("target_entity_id"))
            if not source or not target or source == target:
                continue

            weight_raw = relation.get("weight", 1.0)
            try:
                weight = float(weight_raw or 1.0)
            except Exception:
                weight = 1.0

            graph.add_edge(source, target, weight=max(weight, 0.0001))

        return graph

    @staticmethod
    def _to_igraph(graph_nx: nx.Graph):
        if ig is None:
            raise ImportError("python-igraph no disponible. Instala python-igraph.")

        nodes = list(graph_nx.nodes())
        node_to_index = {node_id: idx for idx, node_id in enumerate(nodes)}
        edge_tuples = []
        weights = []

        for source, target, attrs in graph_nx.edges(data=True):
            edge_tuples.append((node_to_index[source], node_to_index[target]))
            weights.append(float(attrs.get("weight", 1.0)))

        graph_ig = ig.Graph(n=len(nodes), edges=edge_tuples, directed=False)
        if weights:
            graph_ig.es["weight"] = weights

        return graph_ig, nodes

    async def compute_communities(self, tenant_id: UUID) -> dict[int, list[str]]:
        if leidenalg is None:
            raise ImportError("leidenalg no disponible. Instala leidenalg.")

        entities, relations = await self._fetch_tenant_graph_data(tenant_id)
        graph_nx = self._build_networkx_graph(entities, relations)

        if graph_nx.number_of_nodes() == 0:
            logger.warning("No entities found for tenant=%s", tenant_id)
            return {}

        if graph_nx.number_of_edges() == 0:
            logger.warning("No relations found for tenant=%s", tenant_id)
            return {}

        graph_ig, node_ids = self._to_igraph(graph_nx)
        partition = leidenalg.find_partition(
            graph_ig,
            leidenalg.RBConfigurationVertexPartition,
            weights=graph_ig.es["weight"] if graph_ig.ecount() > 0 else None,
            resolution_parameter=self._resolution,
        )

        community_map: dict[int, list[str]] = {}
        for vertex_index, community_id in enumerate(partition.membership):
            community_map.setdefault(int(community_id), []).append(node_ids[vertex_index])

        logger.info(
            "Leiden complete tenant=%s nodes=%s edges=%s communities=%s",
            tenant_id,
            graph_nx.number_of_nodes(),
            graph_nx.number_of_edges(),
            len(community_map),
        )
        return community_map

    async def _summarize_single_community(self, entity_context: list[dict]) -> str:
        lines: list[str] = []
        for entity in entity_context[: self._max_entities_for_prompt]:
            name = str(entity.get("name") or "").strip()
            description = str(entity.get("description") or "").strip()
            if not name and not description:
                continue
            lines.append(f"- {name}: {description}" if description else f"- {name}")

        context_text = "\n".join(lines) if lines else "- Comunidad sin descripciones detalladas"
        system_prompt = (
            "You are a specialized scientific summarizer. Given a list of related entities and "
            "their descriptions, synthesize a comprehensive summary that explains the common "
            "theme, risks, and narrative connecting them. Start with a short title."
        )
        user_prompt = (
            "Community entities and descriptions:\n"
            f"{context_text}\n\n"
            "Write the summary in clear Spanish. Keep it concise but information-dense."
        )

        response = await self._llm.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        return str(response.content).strip()

    async def summarize_communities(
        self,
        tenant_id: UUID,
        partition_map: dict[int, list[str]],
    ) -> dict[int, dict]:
        if not partition_map:
            return {}

        client = await self._get_client()
        tenant_str = str(tenant_id)

        entity_id_set = {entity_id for members in partition_map.values() for entity_id in members}
        entity_ids = list(entity_id_set)
        entities_resp = await client.table("knowledge_entities").select(
            "id,name,description"
        ).eq("tenant_id", tenant_str).in_("id", entity_ids).execute()
        entity_rows = entities_resp.data or []
        entity_by_id = {str(row.get("id")): row for row in entity_rows}

        community_payloads: dict[int, dict] = {}
        semaphore = asyncio.Semaphore(4)

        async def _build_payload(community_id: int, members: list[str]):
            member_entities = [entity_by_id[m] for m in members if m in entity_by_id]
            if not member_entities:
                return

            async with semaphore:
                summary = await self._summarize_single_community(member_entities)
                embedding = None
                try:
                    vectors = await self._embedding_service.embed_texts([summary])
                    embedding = vectors[0] if vectors else None
                except Exception as emb_err:
                    logger.warning("Embedding failed for community=%s: %s", community_id, emb_err)

            community_payloads[community_id] = {
                "tenant_id": tenant_str,
                "community_id": int(community_id),
                "level": 0,
                "summary": summary,
                "embedding": embedding,
                "members": members,
                "metadata": {"size": len(members)},
            }

        await asyncio.gather(*[_build_payload(cid, members) for cid, members in partition_map.items()])
        return community_payloads

    async def persist_communities(self, tenant_id: UUID, communities: dict[int, dict]) -> int:
        if not communities:
            return 0

        client = await self._get_client()
        tenant_str = str(tenant_id)

        await client.table("knowledge_communities").delete().eq("tenant_id", tenant_str).eq("level", 0).execute()

        rows = list(communities.values())
        batch_size = 100
        inserted = 0

        for idx in range(0, len(rows), batch_size):
            batch = rows[idx : idx + batch_size]
            response = await client.table("knowledge_communities").insert(batch).execute()
            inserted += len(response.data or [])

        logger.info("Persisted communities tenant=%s count=%s", tenant_id, inserted)
        return inserted

    async def rebuild_communities(self, tenant_id: UUID) -> dict[str, int]:
        partition_map = await self.compute_communities(tenant_id)
        if not partition_map:
            return {"communities_detected": 0, "communities_persisted": 0}

        payloads = await self.summarize_communities(tenant_id, partition_map)
        persisted = await self.persist_communities(tenant_id, payloads)
        return {
            "communities_detected": len(partition_map),
            "communities_persisted": persisted,
        }
