import json
import os
import logging
import yaml
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Union, Optional, List, Set, Dict, Any, Tuple, Literal
import numpy as np
import importlib
from collections import defaultdict
import argparse
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
from igraph import Graph
import igraph as ig
import numpy as np
from collections import defaultdict
import re
import time

from .llm import _get_llm_class, BaseLLM
from .embedding_model import _get_embedding_model_class, BaseEmbeddingModel
from .embedding_store import EmbeddingStore
from .information_extraction.openie_openai import OpenIE
from .evaluation.retrieval_eval import RetrievalRecall
from .evaluation.qa_eval import QAExactMatch, QAF1Score
from .prompts.linking import get_query_instruction
from .prompts.prompt_template_manager import PromptTemplateManager
from .rerank import DSPyFilter
from .utils.misc_utils import *
from .utils.embed_utils import retrieve_knn
from .utils.typing import Triple
from .utils.config_utils import BaseConfig
from .utils.graph_db_utils import get_graph_db_connector

# 配置logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 创建控制台处理器
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # 设置日志格式
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    
    # 添加处理器到logger
    logger.addHandler(console_handler)

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save_dir", type=str, default="outputs")
    parser.add_argument("--llm_model_name", type=str, default=None)
    parser.add_argument("--llm_base_url", type=str, default=None)
    parser.add_argument("--llm_api_key", type=str, default=None)
    parser.add_argument("--embedding_model_name", type=str, default=None)
    parser.add_argument("--embedding_base_url", type=str, default=None)
    parser.add_argument("--embedding_api_key", type=str, default=None)
    return parser.parse_args()

class HippoRAG:
    """
    HippoRAG: 一个使用基于图的检索方法的RAG系统。
    """
    def __init__(
            self,
            global_config=None,
            save_dir=None,
            llm_model_name=None,
            llm_base_url=None,
            llm_api_key=None,
            embedding_model_name=None,
            embedding_base_url=None,
            embedding_api_key=None,
            auto_save_graph=True,
            direct_db_mode=False,
            ):
        """
        初始化HippoRAG实例。

        Args:
            global_config: 全局配置对象
            save_dir: 保存目录
            llm_model_name: LLM模型名称
            llm_base_url: LLM API基础URL
            llm_api_key: LLM API密钥
            embedding_model_name: 嵌入模型名称
            embedding_base_url: 嵌入API基础URL
            embedding_api_key: 嵌入API密钥
            auto_save_graph: 是否自动保存图。设置为False可以提高性能，但需要确保在重要操作后
                            手动调用force_save_graph()来保存图数据。默认为True以保持向后兼容性。
            direct_db_mode: 是否使用直接数据库模式，在该模式下，将直接在数据库中操作图，而不使用igraph。
                          这可以大大减少内存占用，但需要配置使用图数据库。默认为False。
        """
        if global_config is None:
            self.global_config = BaseConfig()
        else:
            self.global_config = global_config

        # 覆盖配置（如果指定）
        if save_dir is not None:
            self.global_config.save_dir = save_dir

        if llm_model_name is not None:
            self.global_config.llm_name = llm_model_name

        if llm_base_url is not None:
            self.global_config.llm_base_url = llm_base_url

        if llm_api_key is not None:
            self.global_config.llm_api_key = llm_api_key

        if embedding_model_name is not None:
            self.global_config.embedding_model_name = embedding_model_name

        if embedding_base_url is not None:
            self.global_config.embedding_base_url = embedding_base_url

        if embedding_api_key is not None:
            self.global_config.embedding_api_key = embedding_api_key

        self.auto_save_graph = auto_save_graph
        self.direct_db_mode = direct_db_mode
        
        if self.direct_db_mode and not self.global_config.use_graph_db:
            logger.warning("启用direct_db_mode但未配置图数据库，将自动启用图数据库")
            self.global_config.use_graph_db = True
            
        if self.direct_db_mode and self.auto_save_graph:
            logger.info("在直接数据库模式下自动禁用auto_save_graph参数")
            self.auto_save_graph = False
        
        if self.direct_db_mode:
            logger.info("已启用直接数据库模式")

        _print_config = ",\n  ".join([f"{k} = {v}" for k, v in asdict(self.global_config).items()])
        logger.debug(f"HippoRAG init with config:\n  {_print_config}\n")

        #LLM和嵌入模型特定的工作目录在每个指定的保存目录下创建
        llm_label = self.global_config.llm_name.replace("/", "_")
        embedding_label = self.global_config.embedding_model_name.replace("/", "_")
        self.working_dir = os.path.join(self.global_config.save_dir, f"{llm_label}_{embedding_label}")

        if not os.path.exists(self.working_dir):
            logger.info(f"Creating working directory: {self.working_dir}")
            os.makedirs(self.working_dir, exist_ok=True)

        # 初始化图数据库连接器
        self.graph_db = get_graph_db_connector(self.global_config)
        if self.graph_db:
            logger.info(f"已初始化图数据库连接器: {self.global_config.graph_db_type}")
        
        self.llm_model: BaseLLM = _get_llm_class(self.global_config)

        self.openie = OpenIE(llm_model=self.llm_model)

        self.graph = self.initialize_graph()

        self.embedding_model: BaseEmbeddingModel = _get_embedding_model_class(
                embedding_model_name=self.global_config.embedding_model_name
                )(
                global_config=self.global_config,
                embedding_model_name=self.global_config.embedding_model_name
                )
        self.chunk_embedding_store = EmbeddingStore(
                self.embedding_model,
                os.path.join(self.working_dir, "chunk_embeddings"),
                self.global_config.embedding_batch_size, 'chunk'
                )
        self.entity_embedding_store = EmbeddingStore(
                self.embedding_model,
                os.path.join(self.working_dir, "entity_embeddings"),
                self.global_config.embedding_batch_size, 'entity'
                )
        self.fact_embedding_store = EmbeddingStore(
                self.embedding_model,
                os.path.join(self.working_dir, "fact_embeddings"),
                self.global_config.embedding_batch_size, 'fact'
                )

        self.prompt_template_manager = PromptTemplateManager(
                role_mapping={
                    "system": "system",
                    "user": "user",
                    "assistant": "assistant"
                    }
                )

        self.openie_results_path = os.path.join(self.global_config.save_dir,f'openie_results_ner_{self.global_config.llm_name.replace("/", "_")}.json')

        self.rerank_filter = DSPyFilter(self)

        self.ready_to_retrieve = False

        self.ppr_time = 0
        self.rerank_time = 0
        self.all_retrieval_time = 0

        self.ent_node_to_chunk_ids = None


    def initialize_graph(self):
        """
        初始化知识图谱

        从文件或数据库加载现有图结构，或创建新的知识图谱：
        1. 如果配置为使用图数据库，则尝试从数据库加载
        2. 否则，尝试从YAML文件加载
        3. 如果加载失败或配置为重新创建，则初始化新图
        4. 设置图的基本属性（有向性等）
        5. 初始化节点和边的属性
        """
        self._graph_yaml_filename = os.path.join(
                self.working_dir, "graph.yaml"
                )
        self._graph_yaml_backup = os.path.join(
                self.working_dir, "graph.backup.yaml"
                )

        # 直接数据库模式
        if self.direct_db_mode:
            if self.graph_db is None:
                from .utils.graph_db_utils import get_graph_db_connector
                self.graph_db = get_graph_db_connector(self.global_config)
                
            if self.graph_db is None:
                logger.error("无法创建图数据库连接器，请检查配置")
                raise ValueError("在direct_db_mode下必须配置有效的图数据库")
                
            from .utils.graph_db_utils import DirectDBGraph
            logger.info(f"使用直接数据库模式初始化图 (数据库类型: {self.global_config.graph_db_type})")
            return DirectDBGraph(self.graph_db, directed=self.global_config.is_directed_graph)
        
        # 标准模式 (使用igraph)
        preloaded_graph = None

        if not self.global_config.force_index_from_scratch:
            # 如果配置使用图数据库，首先尝试从数据库加载
            if self.global_config.use_graph_db and self.graph_db is not None:
                try:
                    logger.info(f"尝试从{self.global_config.graph_db_type}数据库加载图数据")
                    preloaded_graph = self.graph_db.load_graph()
                    if preloaded_graph is not None:
                        logger.info(f"从{self.global_config.graph_db_type}数据库成功加载图结构: {preloaded_graph.vcount()} 个节点, {preloaded_graph.ecount()} 条边")
                        return preloaded_graph
                    else:
                        logger.warning(f"从{self.global_config.graph_db_type}数据库加载图失败，将尝试从文件加载")
                except Exception as e:
                    logger.warning(f"从{self.global_config.graph_db_type}数据库加载图时出错: {str(e)}")
            
            # 如果从数据库加载失败或未配置数据库，尝试从YAML文件加载
            elif os.path.exists(self._graph_yaml_filename):
                try:
                    logger.info(f"尝试从主文件加载: {self._graph_yaml_filename}")
                    preloaded_graph = self._load_graph_from_yaml(self._graph_yaml_filename)
                except Exception as e:
                    logger.warning(f"从主YAML文件加载失败: {str(e)}")
                    # 尝试从备份文件恢复
                    if os.path.exists(self._graph_yaml_backup):
                        try:
                            logger.info(f"尝试从备份文件加载: {self._graph_yaml_backup}")
                            preloaded_graph = self._load_graph_from_yaml(
                                    self._graph_yaml_backup
                                    )
                            logger.info("成功从备份文件恢复图数据")
                        except Exception as e:
                            logger.warning(f"从备份YAML文件加载失败: {str(e)}")

        if preloaded_graph is None:
            logger.info("创建新的空图")
            return ig.Graph(directed=self.global_config.is_directed_graph)
        else:
            logger.info(
                    f"成功加载图结构: {preloaded_graph.vcount()} 个节点, {preloaded_graph.ecount()} 条边"
                    )
            return preloaded_graph

    def _load_graph_from_yaml(self, yaml_path):
        """从YAML文件加载图数据"""
        try:
            logger.debug(f"开始从 {yaml_path} 加载图数据")
            with open(yaml_path, 'r', encoding='utf-8') as f:
                content = f.read()
                logger.debug(f"文件内容前100个字符: {content[:100]}")
                graph_data = yaml.safe_load(content)
            
            logger.debug(f"成功读取YAML文件，数据类型: {type(graph_data)}")
            if not isinstance(graph_data, dict):
                raise ValueError(f"YAML数据格式错误: 预期是字典类型，实际是 {type(graph_data)}")
            
            if 'nodes' not in graph_data or 'edges' not in graph_data:
                logger.error(f"数据键: {list(graph_data.keys())}")
                raise ValueError(f"YAML数据缺少必要的 'nodes' 或 'edges' 字段")
                
            logger.debug(f"读取到 {len(graph_data['nodes'])} 个节点和 {len(graph_data['edges'])} 条边")
            
            # 创建新图
            g = ig.Graph(directed=self.global_config.is_directed_graph)
            
            # 收集所有节点的属性
            vertex_attrs = defaultdict(list)
            node_map = {}  # 映射节点ID到图中的索引
            
            # 首先添加所有节点并收集属性
            for i, node in enumerate(graph_data['nodes']):
                try:
                    node_id = str(node['id'])
                    idx = len(vertex_attrs['name'])  # 当前节点的索引
                    node_map[node_id] = idx
                    
                    # 收集属性
                    vertex_attrs['name'].append(node_id)
                    for k, v in node.items():
                        if k != 'id':
                            vertex_attrs[str(k)].append(v)
                            
                except Exception as e:
                    logger.error(f"处理第 {i} 个节点时出错: {str(e)}, 节点数据: {node}")
                    raise
            
            # 一次性添加所有节点和属性
            g.add_vertices(len(vertex_attrs['name']))
            for attr_name, attr_values in vertex_attrs.items():
                g.vs[attr_name] = attr_values
            
            logger.debug(f"成功添加 {len(node_map)} 个节点")
            
            # 添加边
            edge_list = []
            edge_weights = []
            for i, edge in enumerate(graph_data['edges']):
                try:
                    source_id = str(edge['source'])
                    target_id = str(edge['target'])
                    if source_id not in node_map:
                        raise KeyError(f"找不到源节点: {source_id}")
                    if target_id not in node_map:
                        raise KeyError(f"找不到目标节点: {target_id}")
                        
                    source_idx = node_map[source_id]
                    target_idx = node_map[target_id]
                    weight = float(edge['weight'])
                    
                    edge_list.append((source_idx, target_idx))
                    edge_weights.append(weight)
                    
                except Exception as e:
                    logger.error(f"处理第 {i} 条边时出错: {str(e)}, 边数据: {edge}")
                    raise
            
            # 一次性添加所有边
            g.add_edges(edge_list)
            g.es['weight'] = edge_weights
            
            logger.debug(f"成功添加 {len(edge_list)} 条边")
            return g
            
        except Exception as e:
            logger.error(f"加载图数据时发生错误: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            raise

    def pre_openie(self,  docs: List[str]):
        logger.info(f"Indexing Documents")
        logger.info(f"Performing OpenIE Offline")

        chunks = self.chunk_embedding_store.get_missing_string_hash_ids(docs)

        all_openie_info, chunk_keys_to_process = self.load_existing_openie(chunks.keys())
        new_openie_rows = {k : chunks[k] for k in chunk_keys_to_process}

        if len(chunk_keys_to_process) > 0:
            new_ner_results_dict, new_triple_results_dict = self.openie.batch_openie(new_openie_rows)
            self.merge_openie_results(all_openie_info, new_openie_rows, new_ner_results_dict, new_triple_results_dict)

        if self.global_config.save_openie:
            self.save_openie_results(all_openie_info)

        assert False, logger.info('Done with OpenIE, run online indexing for future retrieval.')

    def index(self, docs: List[str]):
        """
        索引文档并构建知识图谱

        该方法执行以下步骤：
        1. 使用OpenIE从文档中抽取实体和关系
        2. 为文档块、实体和事实生成向量表示
        3. 构建和更新知识图谱
        4. 保存处理结果

        参数:
            docs (List[str]): 待索引的文档列表
        """
        logger.info(f"Indexing Documents")

        logger.info(f"Performing OpenIE")

        if self.global_config.openie_mode == 'offline':
            self.pre_openie(docs)

        self.chunk_embedding_store.insert_strings(docs)
        chunk_to_rows = self.chunk_embedding_store.get_all_id_to_rows()

        all_openie_info, chunk_keys_to_process = self.load_existing_openie(chunk_to_rows.keys())
        new_openie_rows = {k : chunk_to_rows[k] for k in chunk_keys_to_process}

        if len(chunk_keys_to_process) > 0:
            new_ner_results_dict, new_triple_results_dict = self.openie.batch_openie(new_openie_rows)
            self.merge_openie_results(all_openie_info, new_openie_rows, new_ner_results_dict, new_triple_results_dict)

        if self.global_config.save_openie:
            self.save_openie_results(all_openie_info)

        ner_results_dict, triple_results_dict = reformat_openie_results(all_openie_info)

        assert len(chunk_to_rows) == len(ner_results_dict) == len(triple_results_dict)

        # prepare data_store
        chunk_ids = list(chunk_to_rows.keys())

        chunk_triples = [[text_processing(t) for t in triple_results_dict[chunk_id].triples] for chunk_id in chunk_ids]
        entity_nodes, chunk_triple_entities = extract_entity_nodes(chunk_triples)
        facts = flatten_facts(chunk_triples)

        logger.debug(f"提取到的实体数: {len(entity_nodes)}")
        logger.debug(f"提取到的事实数: {len(facts)}")
        # 打印一些提取到的实体和事实示例
        if len(entity_nodes) > 0:
            logger.debug(f"实体示例: {entity_nodes[:5]}")
        if len(facts) > 0:
            logger.debug(f"事实示例: {facts[:5]}")

        logger.info(f"Encoding Entities")
        self.entity_embedding_store.insert_strings(entity_nodes)

        logger.info(f"Encoding Facts")
        self.fact_embedding_store.insert_strings([str(fact) for fact in facts])
        logger.debug(f"索引后的事实嵌入存储大小: {len(self.fact_embedding_store.get_all_ids())}")

        logger.info(f"Constructing Graph")

        self.node_to_node_stats = {}
        self.ent_node_to_chunk_ids = {}

        self.add_fact_edges(chunk_ids, chunk_triples)
        num_new_chunks = self.add_passage_edges(chunk_ids, chunk_triple_entities)

        if num_new_chunks > 0:
            logger.info(f"Found {num_new_chunks} new chunks to save into graph.")
            self.add_synonymy_edges()

            self.augment_graph()
            self.save_igraph()
            
        # 重置检索就绪状态，确保下次查询时重新加载数据
        self.ready_to_retrieve = False
        logger.info("Index completed. Ready for retrieval after next query.")

    def delete(self, docs_to_delete: List[str]):
        """
        从HippoRAG类的所有数据结构中删除给定的文档。
        请注意，从未被删除的块中索引的三元组和实体将不会被移除。

        参数:
            docs : List[str]
                要删除的文档列表。
        """

        #确保所有必要的结构都已构建。
        if not self.ready_to_retrieve:
            self.prepare_retrieval_objects()

        current_docs = set(self.chunk_embedding_store.get_all_texts())
        docs_to_delete = [doc for doc in docs_to_delete if doc in current_docs]

        #获取要删除的块的ID
        chunk_ids_to_delete = set(
            [self.chunk_embedding_store.text_to_hash_id[chunk] for chunk in docs_to_delete])

        #查找要删除的块中的三元组
        all_openie_info, chunk_keys_to_process = self.load_existing_openie([])
        triples_to_delete = []

        all_openie_info_with_deletes = []

        for openie_doc in all_openie_info:
            if openie_doc['idx'] in chunk_ids_to_delete:
                triples_to_delete.append(openie_doc['extracted_triples'])
            else:
                all_openie_info_with_deletes.append(openie_doc)

        triples_to_delete = flatten_facts(triples_to_delete)

        #过滤掉出现在未修改块中的三元组
        true_triples_to_delete = []

        for triple in triples_to_delete:
            proc_triple = tuple(text_processing(list(triple)))

            doc_ids = self.proc_triples_to_docs[str(proc_triple)]

            non_deleted_docs = doc_ids.difference(chunk_ids_to_delete)

            if len(non_deleted_docs) == 0:
                true_triples_to_delete.append(triple)

        processed_true_triples_to_delete = [[text_processing(list(triple)) for triple in true_triples_to_delete]]
        entities_to_delete, _ = extract_entity_nodes(processed_true_triples_to_delete)
        processed_true_triples_to_delete = flatten_facts(processed_true_triples_to_delete)

        triple_ids_to_delete = set([self.fact_embedding_store.text_to_hash_id[str(triple)] for triple in processed_true_triples_to_delete])

        #过滤掉出现在未修改块中的实体
        ent_ids_to_delete = [self.entity_embedding_store.text_to_hash_id[ent] for ent in entities_to_delete]

        filtered_ent_ids_to_delete = []

        for ent_node in ent_ids_to_delete:
            doc_ids = self.ent_node_to_chunk_ids[ent_node]

            non_deleted_docs = doc_ids.difference(chunk_ids_to_delete)

            if len(non_deleted_docs) == 0:
                filtered_ent_ids_to_delete.append(ent_node)

        logger.info(f"Deleting {len(chunk_ids_to_delete)} Chunks")
        logger.info(f"Deleting {len(triple_ids_to_delete)} Triples")
        logger.info(f"Deleting {len(filtered_ent_ids_to_delete)} Entities")

        self.save_openie_results(all_openie_info_with_deletes)

        self.entity_embedding_store.delete(filtered_ent_ids_to_delete)
        self.fact_embedding_store.delete(triple_ids_to_delete)
        self.chunk_embedding_store.delete(chunk_ids_to_delete)

        #从图中删除节点
        self.graph.delete_vertices(list(filtered_ent_ids_to_delete) + list(chunk_ids_to_delete))
        self.save_igraph()

        self.ready_to_retrieve = False

    def retrieve(
            self,
            queries: List[str],
            num_to_retrieve: int = None,
            gold_docs: List[List[str]] = None
            ) -> List[QuerySolution] | Tuple[List[QuerySolution], Dict]:
        """
        执行检索操作

        该方法使用图增强的检索策略：
        1. 计算查询与事实的相关性
        2. 使用个性化PageRank进行相关性传播
        3. 结合密集检索结果进行排序

        参数:
            queries (List[str]): 查询列表
            num_to_retrieve (int, 可选): 每个查询返回的文档数量
            gold_docs (List[List[str]], 可选): 用于评估的金标文档

        返回:
            Union[List[QuerySolution], Tuple[List[QuerySolution], Dict]]: 
            - 检索结果列表
            - 如果提供gold_docs，还会返回评估指标
        """
        retrieve_start_time = time.time()  # Record start time

        if num_to_retrieve is None:
            num_to_retrieve = self.global_config.retrieval_top_k

        if gold_docs is not None:
            retrieval_recall_evaluator = RetrievalRecall(global_config=self.global_config)

        if not self.ready_to_retrieve:
            self.prepare_retrieval_objects()
            
        # 检查passage_embeddings是否为空
        if len(self.passage_embeddings) == 0:
            logger.info('No passage embeddings available, returning empty results')
            empty_results = [QuerySolution(question=query, docs=[], doc_scores=[]) for query in queries]
            if gold_docs is not None:
                # 如果需要评估，返回空结果和空评估
                return empty_results, {"recall@1": 0.0}
            return empty_results

        self.get_query_embeddings(queries)

        retrieval_results = []

        for q_idx, query in tqdm(enumerate(queries), desc="Retrieving", total=len(queries)):
            rerank_start = time.time()
            
            # 检查fact_embeddings是否为空
            if len(self.fact_embeddings) == 0:
                logger.info('No fact embeddings available, using dense passage retrieval')
                sorted_doc_ids, sorted_doc_scores = self.dense_passage_retrieval(query)
            else:
                query_fact_scores = self.get_fact_scores(query)
                top_k_fact_indices, top_k_facts, rerank_log = self.rerank_facts(query, query_fact_scores)
                rerank_end = time.time()
                
                self.rerank_time += rerank_end - rerank_start

                if len(top_k_facts) == 0:
                    logger.info('No facts found after reranking, return DPR results')
                    sorted_doc_ids, sorted_doc_scores = self.dense_passage_retrieval(query)
                else:
                    sorted_doc_ids, sorted_doc_scores = self.graph_search_with_fact_entities(
                            query=query,
                            link_top_k=self.global_config.linking_top_k,
                            query_fact_scores=query_fact_scores,
                            top_k_facts=top_k_facts,
                            top_k_fact_indices=top_k_fact_indices,
                            passage_node_weight=self.global_config.passage_node_weight)

            # 检查是否返回了空结果
            if len(sorted_doc_ids) == 0:
                logger.info('Retrieved empty result, adding empty QuerySolution')
                retrieval_results.append(QuerySolution(question=query, docs=[], doc_scores=[]))
                continue
            
            top_k_docs = [self.chunk_embedding_store.get_row(self.passage_node_keys[idx])["content"] for idx in sorted_doc_ids[:num_to_retrieve]]

            retrieval_results.append(QuerySolution(question=query, docs=top_k_docs, doc_scores=sorted_doc_scores[:num_to_retrieve]))

        retrieve_end_time = time.time()  # Record end time

        self.all_retrieval_time += retrieve_end_time - retrieve_start_time

        logger.info(f"Total Retrieval Time {self.all_retrieval_time:.2f}s")
        logger.info(f"Total Recognition Memory Time {self.rerank_time:.2f}s")
        logger.info(f"Total PPR Time {self.ppr_time:.2f}s")
        logger.info(f"Total Misc Time {self.all_retrieval_time - (self.rerank_time + self.ppr_time):.2f}s")

        # Evaluate retrieval
        if gold_docs is not None:
            k_list = [1, 2, 5, 10, 20, 30, 50, 100, 150, 200]
            overall_retrieval_result, example_retrieval_results = retrieval_recall_evaluator.calculate_metric_scores(gold_docs=gold_docs, retrieved_docs=[retrieval_result.docs for retrieval_result in retrieval_results], k_list=k_list)
            logger.info(f"Evaluation results for retrieval: {overall_retrieval_result}")

            return retrieval_results, overall_retrieval_result
        else:
            return retrieval_results

    def rag_qa(
            self,
            queries: List[str|QuerySolution],
            gold_docs: List[List[str]] = None,
            gold_answers: List[List[str]] = None
            ) -> Tuple[List[QuerySolution], List[str], List[Dict]] | Tuple[List[QuerySolution], List[str], List[Dict], Dict, Dict]:
        """
        使用HippoRAG 2框架执行检索增强生成问答。

        此方法可以处理基于字符串的查询和预处理的QuerySolution对象。根据输入，
        它仅返回答案或额外评估检索和答案质量，使用召回率@k、精确匹配和F1分数指标。

        参数:
            queries (List[Union[str, QuerySolution]]): 查询列表，可以是字符串或
                QuerySolution实例。如果是字符串，将执行检索。
            gold_docs (Optional[List[List[str]]]): 包含每个查询的金标准文档的列表。
                用于执行文档级评估。默认为None。
            gold_answers (Optional[List[List[str]]]): 包含每个查询的金标准答案的列表。
                如果启用问答(QA)答案评估，则需要此参数。默认为None。

        返回:
            Union[
                Tuple[List[QuerySolution], List[str], List[Dict]],
                Tuple[List[QuerySolution], List[str], List[Dict], Dict, Dict]
            ]: 一个总是包含以下内容的元组:
                - 包含每个查询的答案和元数据的QuerySolution对象列表。
                - 提供的查询的响应消息列表。
                - 每个查询的元数据字典列表。
                如果启用评估，元组还包括:
                - 检索阶段的总体结果字典(如适用)。
                - 包含总体QA评估指标的字典(精确匹配和F1分数)。

        """
        if gold_answers is not None:
            qa_em_evaluator = QAExactMatch(global_config=self.global_config)
            qa_f1_evaluator = QAF1Score(global_config=self.global_config)

        # Retrieving (if necessary)
        overall_retrieval_result = None

        if not isinstance(queries[0], QuerySolution):
            if gold_docs is not None:
                queries, overall_retrieval_result = self.retrieve(queries=queries, gold_docs=gold_docs)
            else:
                queries = self.retrieve(queries=queries)

        # Performing QA
        queries_solutions, all_response_message, all_metadata = self.qa(queries)

        # Evaluating QA
        if gold_answers is not None:
            overall_qa_em_result, example_qa_em_results = qa_em_evaluator.calculate_metric_scores(
                gold_answers=gold_answers, predicted_answers=[qa_result.answer for qa_result in queries_solutions],
                aggregation_fn=np.max)
            overall_qa_f1_result, example_qa_f1_results = qa_f1_evaluator.calculate_metric_scores(
                gold_answers=gold_answers, predicted_answers=[qa_result.answer for qa_result in queries_solutions],
                aggregation_fn=np.max)

            # round off to 4 decimal places for QA results
            overall_qa_em_result.update(overall_qa_f1_result)
            overall_qa_results = overall_qa_em_result
            overall_qa_results = {k: round(float(v), 4) for k, v in overall_qa_results.items()}
            logger.info(f"Evaluation results for QA: {overall_qa_results}")

            # Save retrieval and QA results
            for idx, q in enumerate(queries_solutions):
                q.gold_answers = list(gold_answers[idx])
                if gold_docs is not None:
                    q.gold_docs = gold_docs[idx]

            return queries_solutions, all_response_message, all_metadata, overall_retrieval_result, overall_qa_results
        else:
            return queries_solutions, all_response_message, all_metadata

    def retrieve_dpr(
            self,
            queries: List[str],
            num_to_retrieve: int = None,
            gold_docs: List[List[str]] = None
            ) -> List[QuerySolution] | Tuple[List[QuerySolution], Dict]:
        """
        使用DPR框架执行检索，包括以下步骤：
        - 密集段落打分

        参数:
            queries: List[str]
                要为其检索文档的查询字符串列表。
            num_to_retrieve: int, 可选
                为每个查询检索的最大文档数量。如果未指定，默认为
                全局配置中定义的`retrieval_top_k`值。
            gold_docs: List[List[str]], 可选
                包含与每个查询对应的金标准文档的列表。如果启用检索性能评估
                （全局配置中的`do_eval_retrieval`），则需要此参数。

        返回:
            List[QuerySolution] 或 (List[QuerySolution], Dict)
                如果未启用检索性能评估，则返回QuerySolution对象列表，每个对象包含
                对应查询的检索文档及其得分。如果启用评估，还返回
                包含对检索结果计算的评估指标的字典。

        注释
        -----
        - 重排序后没有相关事实的长查询将默认使用密集段落检索的结果。
        """
        retrieve_start_time = time.time()  # Record start time

        if num_to_retrieve is None:
            num_to_retrieve = self.global_config.retrieval_top_k

        if gold_docs is not None:
            retrieval_recall_evaluator = RetrievalRecall(global_config=self.global_config)

        if not self.ready_to_retrieve:
            self.prepare_retrieval_objects()

        self.get_query_embeddings(queries)

        retrieval_results = []

        for q_idx, query in tqdm(enumerate(queries), desc="Retrieving", total=len(queries)):
            logger.info('No facts found after reranking, return DPR results')
            sorted_doc_ids, sorted_doc_scores = self.dense_passage_retrieval(query)

            top_k_docs = [self.chunk_embedding_store.get_row(self.passage_node_keys[idx])["content"] for idx in
                          sorted_doc_ids[:num_to_retrieve]]

            retrieval_results.append(
                QuerySolution(question=query, docs=top_k_docs, doc_scores=sorted_doc_scores[:num_to_retrieve]))

        retrieve_end_time = time.time()  # Record end time

        self.all_retrieval_time += retrieve_end_time - retrieve_start_time

        logger.info(f"Total Retrieval Time {self.all_retrieval_time:.2f}s")

        # Evaluate retrieval
        if gold_docs is not None:
            k_list = [1, 2, 5, 10, 20, 30, 50, 100, 150, 200]
            overall_retrieval_result, example_retrieval_results = retrieval_recall_evaluator.calculate_metric_scores(
                gold_docs=gold_docs, retrieved_docs=[retrieval_result.docs for retrieval_result in retrieval_results],
                k_list=k_list)
            logger.info(f"Evaluation results for retrieval: {overall_retrieval_result}")

            return retrieval_results, overall_retrieval_result
        else:
            return retrieval_results

    def rag_qa_dpr(
            self,
            queries: List[str|QuerySolution],
            gold_docs: List[List[str]] = None,
            gold_answers: List[List[str]] = None
            ) -> Tuple[List[QuerySolution], List[str], List[Dict]] | Tuple[List[QuerySolution], List[str], List[Dict], Dict, Dict]:
        """
        使用标准DPR框架执行检索增强生成增强的问答。

        此方法可以处理基于字符串的查询和预处理的QuerySolution对象。根据
        其输入，它仅返回答案或额外评估检索和答案质量，使用
        召回率@k、精确匹配和F1分数指标。

        参数:
            queries (List[Union[str, QuerySolution]]): 查询列表，可以是字符串或
                QuerySolution实例。如果是字符串，将执行检索。
            gold_docs (Optional[List[List[str]]]): 包含每个查询的金标准文档的列表。
                如果要执行文档级评估，则使用此参数。默认为None。
            gold_answers (Optional[List[List[str]]]): 包含每个查询的金标准答案的列表。
                如果启用问答(QA)答案评估，则需要此参数。默认为None。

        返回:
            Union[
                Tuple[List[QuerySolution], List[str], List[Dict]],
                Tuple[List[QuerySolution], List[str], List[Dict], Dict, Dict]
            ]: 一个总是包含以下内容的元组：
                - 包含每个查询的答案和元数据的QuerySolution对象列表。
                - 提供的查询的响应消息列表。
                - 每个查询的元数据字典列表。
                如果启用评估，元组还包括：
                - 检索阶段的总体结果字典（如适用）。
                - 包含总体QA评估指标的字典（精确匹配和F1分数）。

        """
        if gold_answers is not None:
            qa_em_evaluator = QAExactMatch(global_config=self.global_config)
            qa_f1_evaluator = QAF1Score(global_config=self.global_config)

        # Retrieving (if necessary)
        overall_retrieval_result = None

        if not isinstance(queries[0], QuerySolution):
            if gold_docs is not None:
                queries, overall_retrieval_result = self.retrieve_dpr(queries=queries, gold_docs=gold_docs)
            else:
                queries = self.retrieve_dpr(queries=queries)

        # Performing QA
        queries_solutions, all_response_message, all_metadata = self.qa(queries)

        # Evaluating QA
        if gold_answers is not None:
            overall_qa_em_result, example_qa_em_results = qa_em_evaluator.calculate_metric_scores(
                gold_answers=gold_answers, predicted_answers=[qa_result.answer for qa_result in queries_solutions],
                aggregation_fn=np.max)
            overall_qa_f1_result, example_qa_f1_results = qa_f1_evaluator.calculate_metric_scores(
                gold_answers=gold_answers, predicted_answers=[qa_result.answer for qa_result in queries_solutions],
                aggregation_fn=np.max)

            # round off to 4 decimal places for QA results
            overall_qa_em_result.update(overall_qa_f1_result)
            overall_qa_results = overall_qa_em_result
            overall_qa_results = {k: round(float(v), 4) for k, v in overall_qa_results.items()}
            logger.info(f"Evaluation results for QA: {overall_qa_results}")

            # Save retrieval and QA results
            for idx, q in enumerate(queries_solutions):
                q.gold_answers = list(gold_answers[idx])
                if gold_docs is not None:
                    q.gold_docs = gold_docs[idx]

            return queries_solutions, all_response_message, all_metadata, overall_retrieval_result, overall_qa_results
        else:
            return queries_solutions, all_response_message, all_metadata

    def qa(self, queries: List[QuerySolution]) -> Tuple[List[QuerySolution], List[str], List[Dict]]:
        """
        使用提供的查询解决方案集和语言模型执行问答(QA)推理。

        参数:
            queries: List[QuerySolution]
                一个QuerySolution对象列表，包含用户查询、检索到的文档和其他相关信息。

        返回:
            Tuple[List[QuerySolution], List[str], List[Dict]]
                一个包含以下内容的元组:
                - 一个更新的QuerySolution对象列表，其中嵌入了预测的答案。
                - 来自语言模型的原始响应消息列表。
                - 与结果相关的元数据字典列表。
        """
        #运行QA推理
        all_qa_messages = []

        for query_solution in tqdm(queries, desc="Collecting QA prompts"):

            # obtain the retrieved docs
            retrieved_passages = query_solution.docs[:self.global_config.qa_top_k]

            prompt_user = ''
            for passage in retrieved_passages:
                prompt_user += f'Wikipedia Title: {passage}\n\n'
            prompt_user += 'Question: ' + query_solution.question + '\nThought: '

            if self.prompt_template_manager.is_template_name_valid(name=f'rag_qa_{self.global_config.dataset}'):
                # find the corresponding prompt for this dataset
                prompt_dataset_name = self.global_config.dataset
            else:
                # the dataset does not have a customized prompt template yet
                logger.debug(
                    f"rag_qa_{self.global_config.dataset} does not have a customized prompt template. Using MUSIQUE's prompt template instead.")
                prompt_dataset_name = 'musique'
            all_qa_messages.append(
                self.prompt_template_manager.render(name=f'rag_qa_{prompt_dataset_name}', prompt_user=prompt_user))

        all_qa_results = [self.llm_model.infer(qa_messages) for qa_messages in tqdm(all_qa_messages, desc="QA Reading")]

        all_response_message, all_metadata, all_cache_hit = zip(*all_qa_results)
        all_response_message, all_metadata = list(all_response_message), list(all_metadata)

        #处理响应并提取预测的答案。
        queries_solutions = []
        for query_solution_idx, query_solution in tqdm(enumerate(queries), desc="Extraction Answers from LLM Response"):
            response_content = all_response_message[query_solution_idx]
            try:
                pred_ans = response_content.split('Answer:')[1].strip()
            except Exception as e:
                logger.warning(f"Error in parsing the answer from the raw LLM QA inference response: {str(e)}!")
                pred_ans = response_content

            query_solution.answer = pred_ans
            queries_solutions.append(query_solution)

        return queries_solutions, all_response_message, all_metadata

    def add_fact_edges(self, chunk_ids: List[str], chunk_triples: List[Tuple]):
        """
        将给定三元组的事实边添加到图中。

        该方法处理三元组块，计算实体和关系的唯一标识符，
        并更新各种内部统计信息以构建和维护图结构。实体根据
        它们的关系进行唯一标识和链接。

        参数:
            chunk_ids: List[str]
                正在处理的块的唯一标识符列表。
            chunk_triples: List[Tuple]
                要处理的表示三元组的元组列表。每个三元组
                由主语、谓语和宾语组成。

        引发:
            在提供的函数逻辑中没有明确引发异常。
        """

        if "name" in self.graph.vs:
            current_graph_nodes = set(self.graph.vs["name"])
        else:
            current_graph_nodes = set()

        logger.info(f"Adding OpenIE triples to graph.")

        for chunk_key, triples in tqdm(zip(chunk_ids, chunk_triples)):
            entities_in_chunk = set()

            if chunk_key not in current_graph_nodes:
                for triple in triples:
                    triple = tuple(triple)

                    node_key = compute_mdhash_id(content=triple[0], prefix=("entity-"))
                    node_2_key = compute_mdhash_id(content=triple[2], prefix=("entity-"))

                    self.node_to_node_stats[(node_key, node_2_key)] = self.node_to_node_stats.get(
                        (node_key, node_2_key), 0.0) + 1
                    self.node_to_node_stats[(node_2_key, node_key)] = self.node_to_node_stats.get(
                        (node_2_key, node_key), 0.0) + 1

                    entities_in_chunk.add(node_key)
                    entities_in_chunk.add(node_2_key)

                for node in entities_in_chunk:
                    self.ent_node_to_chunk_ids[node] = self.ent_node_to_chunk_ids.get(node, set()).union(set([chunk_key]))

    def add_passage_edges(self, chunk_ids: List[str], chunk_triple_entities: List[List[str]]):
        """
        添加文档段落节点到短语节点的边连接

        该方法负责处理文档块ID列表及其对应的三元组实体，主要功能包括：
        1. 计算并添加文档节点与短语节点之间的新边
        2. 更新节点间统计信息映射
        3. 统计新增的文档节点数量

        参数:
            chunk_ids : List[str]
                表示文档段落节点的标识符列表
            chunk_triple_entities : List[List[str]]
                每个子列表包含与chunk_ids列表中对应文档块相关的实体（字符串）

        返回:
            int: 新增加的文档节点数量
        """

        if "name" in self.graph.vs.attribute_names():
            current_graph_nodes = set(self.graph.vs["name"])
        else:
            current_graph_nodes = set()

        num_new_chunks = 0

        logger.info(f"Connecting passage nodes to phrase nodes.")

        for idx, chunk_key in tqdm(enumerate(chunk_ids)):

            if chunk_key not in current_graph_nodes:
                for chunk_ent in chunk_triple_entities[idx]:
                    node_key = compute_mdhash_id(chunk_ent, prefix="entity-")

                    self.node_to_node_stats[(chunk_key, node_key)] = 1.0

                num_new_chunks += 1

        return num_new_chunks

    def add_synonymy_edges(self):
        """
        在图中的相似节点之间添加同义词边来增强连接性，通过识别和链接同义词实体。

        此方法执行关键操作来计算和添加同义词边。首先检索所有节点的嵌入，然后进行
        最近邻(KNN)搜索以找到相似节点。这些相似节点基于分数阈值进行识别，并添加边
        来表示同义词关系。

        属性:
            entity_id_to_row: dict (在函数内填充)。将每个实体ID映射到其对应的行数据，其中行
                              包含用于比较的实体的`content`。
            entity_embedding_store: 管理与实体相关的所有行的文本和嵌入的检索。
            global_config: 配置对象，定义参数如`synonymy_edge_topk`、`synonymy_edge_sim_threshold`、
                           `synonymy_edge_query_batch_size`和`synonymy_edge_key_batch_size`。
            node_to_node_stats: dict。存储节点之间边的分数，表示它们的关系。

        """
        logger.info(f"Expanding graph with synonymy edges")

        self.entity_id_to_row = self.entity_embedding_store.get_all_id_to_rows()
        entity_node_keys = list(self.entity_id_to_row.keys())

        logger.info(f"Performing KNN retrieval for each phrase nodes ({len(entity_node_keys)}).")

        entity_embs = self.entity_embedding_store.get_embeddings(entity_node_keys)

        # Here we build synonymy edges only between newly inserted phrase nodes and all phrase nodes in the storage to reduce cost for incremental graph updates
        query_node_key2knn_node_keys = retrieve_knn(
                query_ids=entity_node_keys,
                key_ids=entity_node_keys,
                query_vecs=entity_embs,
                key_vecs=entity_embs,
                k=self.global_config.synonymy_edge_topk,
                query_batch_size=self.global_config.synonymy_edge_query_batch_size,
                key_batch_size=self.global_config.synonymy_edge_key_batch_size
                )

        num_synonym_triple = 0
        synonym_candidates = []  # [(node key, [(synonym node key, corresponding score), ...]), ...]

        for node_key in tqdm(query_node_key2knn_node_keys.keys(), total=len(query_node_key2knn_node_keys)):
            synonyms = []

            entity = self.entity_id_to_row[node_key]["content"]

            if len(re.sub('[^A-Za-z0-9]', '', entity)) > 2:
                nns = query_node_key2knn_node_keys[node_key]

                num_nns = 0
                for nn, score in zip(nns[0], nns[1]):
                    if score < self.global_config.synonymy_edge_sim_threshold or num_nns > 100:
                        break

                    nn_phrase = self.entity_id_to_row[nn]["content"]

                    if nn != node_key and nn_phrase != '':
                        sim_edge = (node_key, nn)
                        synonyms.append((nn, score))
                        num_synonym_triple += 1

                        self.node_to_node_stats[sim_edge] = score  # Need to seriously discuss on this
                        num_nns += 1

            synonym_candidates.append((node_key, synonyms))

    def load_existing_openie(self, chunk_keys: List[str]) -> Tuple[List[dict], Set[str]]:
        """
        加载已有的开放信息抽取结果

        从文件中加载之前处理过的OpenIE结果，并识别需要新处理的文档块。
        如果配置为强制重新处理或文件不存在，则准备处理所有文档块。

        参数:
            chunk_keys (List[str]): 文档块的标识符列表

        返回:
            Tuple[List[dict], Set[str]]:
            - 已有的OpenIE信息列表
            - 需要处理的文档块集合
        """

        # combine openie_results with contents already in file, if file exists
        chunk_keys_to_save = set()

        if not self.global_config.force_openie_from_scratch and os.path.isfile(self.openie_results_path):
            openie_results = json.load(open(self.openie_results_path))
            all_openie_info = openie_results.get('docs', [])

            #Standardizing indices for OpenIE Files.

            renamed_openie_info = []
            for openie_info in all_openie_info:
                openie_info['idx'] = compute_mdhash_id(openie_info['passage'], 'chunk-')
                renamed_openie_info.append(openie_info)

            all_openie_info = renamed_openie_info

            existing_openie_keys = set([info['idx'] for info in all_openie_info])

            for chunk_key in chunk_keys:
                if chunk_key not in existing_openie_keys:
                    chunk_keys_to_save.add(chunk_key)
        else:
            all_openie_info = []
            chunk_keys_to_save = chunk_keys

        return all_openie_info, chunk_keys_to_save

    def merge_openie_results(
            self,
            all_openie_info: List[dict],
            chunks_to_save: Dict[str, dict],
            ner_results_dict: Dict[str, NerRawOutput],
            triple_results_dict: Dict[str, TripleRawOutput]
            ) -> List[dict]:
        """
        合并OpenIE抽取结果

        将新处理的OpenIE结果与原有结果合并，包括：
        - 命名实体识别结果
        - 关系三元组抽取结果
        - 文档内容和元数据

        参数:
            all_openie_info (List[dict]): 现有的OpenIE信息列表
            chunks_to_save (Dict[str, dict]): 待处理的文档块
            ner_results_dict (Dict[str, NerRawOutput]): 命名实体识别结果
            triple_results_dict (Dict[str, TripleRawOutput]): 关系三元组结果

        返回:
            List[dict]: 合并后的OpenIE信息列表
        """

        for chunk_key, row in chunks_to_save.items():
            passage = row['content']
            chunk_openie_info = {
                'idx': chunk_key,
                'passage': passage,
                'extracted_entities': ner_results_dict[chunk_key].unique_entities,
                'extracted_triples': triple_results_dict[chunk_key].triples
            }
            all_openie_info.append(chunk_openie_info)

        return all_openie_info

    def save_openie_results(self, all_openie_info: List[dict]):
        """
        保存OpenIE处理结果

        将OpenIE的处理结果保存到文件，同时计算一些统计信息：
        - 实体的平均字符长度
        - 实体的平均词数
        - 总实体数量

        参数:
            all_openie_info (List[dict]): OpenIE处理结果列表
        """

        sum_phrase_chars = sum([len(e) for chunk in all_openie_info for e in chunk['extracted_entities']])
        sum_phrase_words = sum([len(e.split()) for chunk in all_openie_info for e in chunk['extracted_entities']])
        num_phrases = sum([len(chunk['extracted_entities']) for chunk in all_openie_info])

        if len(all_openie_info) > 0:
            if num_phrases > 0:
                avg_ent_chars = round(sum_phrase_chars / num_phrases, 4)
                avg_ent_words = round(sum_phrase_words / num_phrases, 4)
            else:
                avg_ent_chars = 0
                avg_ent_words = 0
                
            openie_dict = {'docs': all_openie_info, 'avg_ent_chars': avg_ent_chars,
                           'avg_ent_words': avg_ent_words}
            with open(self.openie_results_path, 'w') as f:
                json.dump(openie_dict, f)
            logger.info(f"OpenIE results saved to {self.openie_results_path}")

    def augment_graph(self):
        """
        增强知识图谱

        通过添加新的节点和边来扩展图结构：
        1. 添加新的实体和文档节点
        2. 添加节点间的关系边
        3. 更新图的统计信息
        """

        self.add_new_nodes()
        self.add_new_edges()

        logger.info(f"Graph construction completed!")
        print(self.get_graph_info())

    def add_new_nodes(self):
        """
        添加新节点到图中

        从实体和文档块向量存储中识别并添加新节点：
        1. 检查现有节点
        2. 识别新节点
        3. 批量添加节点及其属性
        """

        existing_nodes = {v["name"]: v for v in self.graph.vs if "name" in v.attributes()}

        entity_to_row = self.entity_embedding_store.get_all_id_to_rows()
        passage_to_row = self.chunk_embedding_store.get_all_id_to_rows()

        node_to_rows = entity_to_row
        node_to_rows.update(passage_to_row)

        new_nodes = {}
        for node_id, node in node_to_rows.items():
            node['name'] = node_id
            if node_id not in existing_nodes:
                for k, v in node.items():
                    if k not in new_nodes:
                        new_nodes[k] = []
                    new_nodes[k].append(v)

        if len(new_nodes) > 0:
            self.graph.add_vertices(n=len(next(iter(new_nodes.values()))), attributes=new_nodes)

    def add_new_edges(self):
        """
        添加新边到图中，对重复边进行合并而不是创建新边

        处理node_to_node_stats中的边信息：
        1. 构建邻接列表
        2. 检查边是否已存在，如存在则更新权重
        3. 只为不存在的边添加新边
        """

        # 先获取图中所有现有边，构建(source, target) -> edge_index的映射
        existing_edges = {}
        for edge_index, edge in enumerate(self.graph.es):
            source_name = self.graph.vs[edge.source]["name"]
            target_name = self.graph.vs[edge.target]["name"]
            existing_edges[(source_name, target_name)] = edge_index

        logger.info(f"已有边数量: {len(existing_edges)}")

        graph_adj_list = defaultdict(dict)
        graph_inverse_adj_list = defaultdict(dict)
        edge_source_node_keys = []
        edge_target_node_keys = []
        edge_metadata = []
        
        # 构建需要添加的边列表
        for edge, weight in self.node_to_node_stats.items():
            if edge[0] == edge[1]: continue
            graph_adj_list[edge[0]][edge[1]] = weight
            graph_inverse_adj_list[edge[1]][edge[0]] = weight

            edge_source_node_keys.append(edge[0])
            edge_target_node_keys.append(edge[1])
            edge_metadata.append({
                "weight": weight
            })

        # 分成两部分处理：更新已存在的边和添加新边
        edges_to_update = []  # 元素格式为 (edge_index, new_weight)
        valid_edges, valid_weights = [], {"weight": []}
        current_node_ids = set(self.graph.vs["name"])
        
        num_skipped = 0
        num_updated = 0
        num_added = 0
        
        for source_node_id, target_node_id, edge_d in zip(edge_source_node_keys, edge_target_node_keys, edge_metadata):
            # 跳过无效节点边
            if source_node_id not in current_node_ids or target_node_id not in current_node_ids:
                num_skipped += 1
                continue
                
            weight = edge_d.get("weight", 1.0)
            edge_key = (source_node_id, target_node_id)
            
            # 检查边是否已存在
            if edge_key in existing_edges:
                # 更新已存在边的权重
                edge_index = existing_edges[edge_key]
                current_weight = self.graph.es[edge_index]["weight"]
                # 合并策略：取较大值或求和
                # new_weight = max(current_weight, weight)  # 或者
                new_weight = current_weight + weight
                edges_to_update.append((edge_index, new_weight))
                num_updated += 1
            else:
                # 添加新边
                valid_edges.append((source_node_id, target_node_id))
                valid_weights["weight"].append(weight)
                num_added += 1
        
        # 批量更新已存在的边
        if edges_to_update:
            for edge_index, new_weight in edges_to_update:
                self.graph.es[edge_index]["weight"] = new_weight
                
        # 批量添加新边
        if valid_edges:
            self.graph.add_edges(
                valid_edges,
                attributes=valid_weights
            )
            
        logger.info(f"边处理统计: 跳过 {num_skipped} 条, 更新 {num_updated} 条, 添加 {num_added} 条")
        logger.info(f"图中边总数: {self.graph.ecount()}")

    def verify_graph_data(self, graph_data):
        """
        验证图数据的完整性和有效性

        对YAML格式的图数据进行全面检查：
        1. 统计基本信息（节点数、边数）
        2. 检查节点的所有属性
        3. 检查边的所有属性
        4. 验证特殊类型节点（如实体节点、文档节点）

        参数:
            graph_data: YAML格式的图数据

        返回:
            dict: 包含验证结果的详细统计信息
        """
        logger.info("开始验证图数据完整性...")
        
        # 1. 基础统计信息
        node_count = len(graph_data['nodes'])
        edge_count = len(graph_data['edges'])
        logger.info(f"YAML数据统计: {node_count} 个节点, {edge_count} 条边")
        logger.info(f"原图统计: {self.graph.vcount()} 个节点, {self.graph.ecount()} 条边")
        
        # 2. 检查节点属性
        sample_nodes = graph_data['nodes'][:5]
        logger.debug(f"节点示例(前5个):")
        for node in sample_nodes:
            logger.debug(f"节点ID: {node['id']}")
            logger.debug(f"属性: {', '.join(node.keys())}")
            
        # 3. 检查边属性
        sample_edges = graph_data['edges'][:5]
        logger.debug(f"边示例(前5个):")
        for edge in sample_edges:
            logger.debug(f"{edge['source']} -> {edge['target']} (权重: {edge['weight']})")
            
        # 4. 检查特殊节点类型
        entity_nodes = [n for n in graph_data['nodes'] if n['id'].startswith('entity-')]
        chunk_nodes = [n for n in graph_data['nodes'] if not n['id'].startswith('entity-')]
        logger.debug(f"实体节点数: {len(entity_nodes)}")
        logger.debug(f"文档块节点数: {len(chunk_nodes)}")
        
        return {
            'node_count': node_count,
            'edge_count': edge_count,
            'entity_node_count': len(entity_nodes),
            'chunk_node_count': len(chunk_nodes),
            'node_attributes': list(sample_nodes[0].keys()) if sample_nodes else [],
            'edge_attributes': list(sample_edges[0].keys()) if sample_edges else []
        }

    def convert_to_native_types(self, obj):
        """
        转换数据类型为Python原生类型

        将特殊类型（如numpy类型）转换为Python原生类型：
        1. numpy数值类型转换为Python数值
        2. 递归处理字典和列表
        3. 处理numpy数组

        参数:
            obj: 需要转换的对象

        返回:
            转换后的Python原生类型对象
        """
        if isinstance(obj, (np.generic,)):
            return obj.item()
        elif isinstance(obj, dict):
            return {str(k): self.convert_to_native_types(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self.convert_to_native_types(item) for item in obj]
        elif hasattr(obj, 'tolist'):  # 处理numpy数组
            return obj.tolist()
        return obj

    def save_igraph(self, force=False):
        """
        保存图数据到数据库或YAML文件
        
        如果配置使用图数据库，则保存到数据库；否则保存到YAML文件
        
        参数:
            force (bool): 如果为True，则无论auto_save_graph设置如何，都强制保存图
        """
        if self.direct_db_mode:
            logger.debug("在直接数据库模式下，图数据已经存储在数据库中，无需额外保存")
            return
            
        if not self.auto_save_graph and not force:
            logger.debug("图自动保存已禁用，跳过保存图操作")
            return
            
        logger.info(
            f"正在保存图数据: {len(self.graph.vs())} 个节点, {len(self.graph.es())} 条边"
        )
        
        # 首先尝试保存到图数据库
        if self.global_config.use_graph_db and self.graph_db is not None:
            try:
                if self.graph_db.save_graph(self.graph):
                    logger.info(f"成功保存图数据到{self.global_config.graph_db_type}数据库")
                    return
                else:
                    logger.warning(f"保存到{self.global_config.graph_db_type}数据库失败，将尝试保存为文件")
            except Exception as e:
                logger.warning(f"保存到{self.global_config.graph_db_type}数据库时出错: {str(e)}")
        else:
            # 如果未使用数据库或保存到数据库失败，使用YAML文件保存
            # 准备图数据
            graph_data = {
                'nodes': [
                    self.convert_to_native_types({
                        'id': v['name'],
                        **{k: v[k] for k in v.attribute_names() if k != 'name'}
                    }) for v in self.graph.vs
                ],
                'edges': [
                    self.convert_to_native_types({
                        'source': self.graph.vs[e.source]['name'],
                        'target': self.graph.vs[e.target]['name'],
                        'weight': e['weight']
                    }) for e in self.graph.es
                ]
            }

            # 如果logger是debug模式，则验证数据完整性
            if logger.getEffectiveLevel() == logging.DEBUG:
                # 验证数据完整性
                stats = self.verify_graph_data(graph_data)
                logger.info("数据验证结果:")
                for k, v in stats.items():
                    logger.info(f"{k}: {v}")

            # 保存主文件
            with open(self._graph_yaml_filename, 'w', encoding='utf-8') as f:
                yaml.safe_dump(graph_data, f, allow_unicode=True)
            
            # 保存备份文件
            with open(self._graph_yaml_backup, 'w', encoding='utf-8') as f:
                yaml.safe_dump(graph_data, f, allow_unicode=True)

        logger.info(f"图数据保存到文件完成!")

    def get_graph_info(self) -> Dict:
        """
        获取图结构的统计信息

        返回当前知识图谱的详细统计信息，包括：
        - 短语节点数量
        - 文档段落节点数量
        - 总节点数
        - 抽取的三元组数量
        - 包含文档节点的三元组数量
        - 同义三元组数量
        - 总三元组数量

        返回:
            Dict: 包含各类统计信息的字典
        """
        graph_info = {}

        # get # of phrase nodes
        phrase_nodes_keys = self.entity_embedding_store.get_all_ids()
        graph_info["num_phrase_nodes"] = len(set(phrase_nodes_keys))

        # get # of passage nodes
        passage_nodes_keys = self.chunk_embedding_store.get_all_ids()
        graph_info["num_passage_nodes"] = len(set(passage_nodes_keys))

        # get # of total nodes
        graph_info["num_total_nodes"] = graph_info["num_phrase_nodes"] + graph_info["num_passage_nodes"]

        # get # of extracted triples
        graph_info["num_extracted_triples"] = len(self.fact_embedding_store.get_all_ids())

        num_triples_with_passage_node = 0
        passage_nodes_set = set(passage_nodes_keys)
        num_triples_with_passage_node = sum(
            1 for node_pair in self.node_to_node_stats
            if node_pair[0] in passage_nodes_set or node_pair[1] in passage_nodes_set
        )
        graph_info['num_triples_with_passage_node'] = num_triples_with_passage_node

        graph_info['num_synonymy_triples'] = len(self.node_to_node_stats) - graph_info[
            "num_extracted_triples"] - num_triples_with_passage_node

        # get # of total triples
        graph_info["num_total_triples"] = len(self.node_to_node_stats)

        return graph_info

    def prepare_retrieval_objects(self):
        """
        准备检索所需的数据结构

        该方法初始化和准备检索过程中需要的各种数据结构，包括：
        1. 加载实体、段落和事实的向量表示
        2. 建立节点索引映射
        3. 准备图结构的顶点索引
        4. 加载OpenIE结果并建立文档关系映射

        这个方法通常在第一次检索之前调用，用于确保所有必要的数据结构都已就绪。
        """

        logger.info("Preparing for fast retrieval.")

        logger.info("Loading keys.")
        self.query_to_embedding: Dict = {'triple': {}, 'passage': {}}

        # 获取所有节点键
        all_entity_keys = list(self.entity_embedding_store.get_all_ids())  # 所有实体节点键
        all_passage_keys = list(self.chunk_embedding_store.get_all_ids())  # 所有段落节点键
        self.fact_node_keys = list(self.fact_embedding_store.get_all_ids())
        
        logger.debug(f"从向量存储加载的实体节点总数: {len(all_entity_keys)}")
        logger.debug(f"从向量存储加载的段落节点总数: {len(all_passage_keys)}")
        logger.debug(f"从向量存储加载的事实节点总数: {len(self.fact_node_keys)}")
        
        # 获取图中节点情况
        graph_count = self.graph.vcount()
        logger.debug(f"图中的顶点总数: {graph_count}")
        
        # 检查节点计数是否一致
        expected_vcount = len(all_entity_keys) + len(all_passage_keys)
        actual_vcount = graph_count
        
        # 处理重复节点问题
        # 确保没有重复的实体和段落节点ID (可能存在同一节点同时在实体和段落中出现的情况)
        duplicate_keys = set(all_entity_keys).intersection(set(all_passage_keys))
        if duplicate_keys:
            logger.warning(f"发现 {len(duplicate_keys)} 个重复节点，同时存在于实体和段落存储中")
            # 这里使用集合操作去除重复节点
            unique_entity_keys = list(set(all_entity_keys) - duplicate_keys)
            unique_passage_keys = list(set(all_passage_keys))
            logger.debug(f"去重后的实体节点数: {len(unique_entity_keys)}")
            logger.debug(f"去重后的段落节点数: {len(unique_passage_keys)}")
            # 更新为去重后的列表
            self.entity_node_keys = unique_entity_keys
            self.passage_node_keys = unique_passage_keys
        else:
            # 如果没有重复，直接使用原始列表
            self.entity_node_keys = all_entity_keys
            self.passage_node_keys = all_passage_keys
        
        # 重新计算预期顶点数
        expected_vcount = len(self.entity_node_keys) + len(self.passage_node_keys)
        
        # 打印一些事实示例
        if len(self.fact_node_keys) > 0:
            sample_facts = self.fact_embedding_store.get_rows(self.fact_node_keys[:5])
            logger.debug(f"事实示例:")
            for fact_id, fact_row in sample_facts.items():
                logger.debug(f"  {fact_id}: {fact_row['content']}")
        
        if expected_vcount != actual_vcount:
            logger.error(f"节点数量不匹配：实体节点 {len(self.entity_node_keys)} + 段落节点 {len(self.passage_node_keys)} = {expected_vcount} != 图顶点总数 {actual_vcount}")
            
            # 检查图类型，处理直接数据库模式
            # 对于直接数据库模式，使用适合的方式获取节点属性
            graph_nodes = set()
            
            if "name" in self.graph.vs.attribute_names():
                # 获取图中的节点名称
                graph_nodes = set(self.graph.vs["name"])
            else:
                logger.warning("图中没有'name'属性，无法进行自动修复")
                graph_nodes = set()
            
            if graph_nodes:
                entity_passage_nodes = set(self.entity_node_keys + self.passage_node_keys)
                
                # 找出多余和缺失的节点
                extra_nodes = graph_nodes - entity_passage_nodes
                missing_nodes = entity_passage_nodes - graph_nodes
                
                logger.warning(f"图中有 {len(extra_nodes)} 个多余节点和 {len(missing_nodes)} 个缺失节点")
                
                # 如果找不到明确的多余或缺失节点，但数量不匹配
                if not extra_nodes and not missing_nodes and expected_vcount != actual_vcount:
                    logger.warning("无法确定具体的多余或缺失节点，但节点数量不匹配")
                    logger.warning("这可能是由于实体节点和段落节点有重叠导致。尝试进一步分析...")
                    
                    # 再次检查是否有冗余节点
                    total_unique_nodes = len(set(self.entity_node_keys + self.passage_node_keys))
                    if total_unique_nodes != expected_vcount:
                        logger.warning(f"合并实体和段落节点后的唯一节点数 ({total_unique_nodes}) 与原始计数 ({expected_vcount}) 不同")
                        logger.warning("检测到节点重叠，调整预期节点数...")
                        
                        # 更新预期节点数为唯一节点数
                        expected_vcount = total_unique_nodes
                        logger.info(f"调整后的预期节点数: {expected_vcount}")
                        
                        # 重新计算多余和缺失节点
                        extra_nodes = graph_nodes - entity_passage_nodes
                        missing_nodes = entity_passage_nodes - graph_nodes
                        logger.warning(f"重新计算: 图中有 {len(extra_nodes)} 个多余节点和 {len(missing_nodes)} 个缺失节点")
                    
                    # 如果依然找不到原因，考虑图可能有空节点
                    if not extra_nodes and not missing_nodes and total_unique_nodes != actual_vcount:
                        empty_names = sum(1 for n in self.graph.vs if "name" not in n.attributes() or n["name"] is None)
                        if empty_names > 0:
                            logger.warning(f"图中存在 {empty_names} 个无名称节点，这可能导致节点计数不一致")
                            
                            # 尝试为无名称节点添加名称
                            node_counter = 0
                            for v_idx, v in enumerate(self.graph.vs):
                                if "name" not in v.attributes() or v["name"] is None:
                                    new_name = f"auto_named_node_{node_counter}"
                                    self.graph.vs[v_idx]["name"] = new_name
                                    node_counter += 1
                            
                            if node_counter > 0:
                                logger.info(f"已为 {node_counter} 个无名节点添加自动名称")
                                self.save_igraph()
                
                # 尝试修复：删除多余节点
                if extra_nodes and len(extra_nodes) < 100:  # 安全检查，避免大规模删除
                    nodes_to_delete = []
                    for node_name in extra_nodes:
                        try:
                            node_idx = self.graph.vs.find(name=node_name).index
                            nodes_to_delete.append(node_idx)
                        except:
                            logger.warning(f"无法找到节点 {node_name} 的索引")
                    
                    if nodes_to_delete:
                        logger.info(f"自动修复：删除 {len(nodes_to_delete)} 个多余节点")
                        self.graph.delete_vertices(nodes_to_delete)
                        self.save_igraph()
                        logger.info(f"删除后图的顶点数：{self.graph.vcount()}")
                
                # 添加缺失节点
                if missing_nodes:
                    logger.info(f"自动修复：添加 {len(missing_nodes)} 个缺失节点")
                    self.graph.add_vertices(len(missing_nodes), attributes={"name": list(missing_nodes)})
                    self.save_igraph()
                    logger.info(f"添加后图的顶点数：{self.graph.vcount()}")
                
                # 重新检查
                if len(set(self.entity_node_keys + self.passage_node_keys)) == self.graph.vcount():
                    logger.info("自动修复成功：节点数量现在匹配")
                else:
                    logger.warning("自动修复后节点数量仍不匹配，继续执行但可能会出现问题")
                    
                    # 最后的修复措施：强制使实体节点和段落节点匹配图的节点数
                    # 警告：这可能导致数据不一致，但能让系统继续运行
                    if self.global_config.auto_fix_node_mismatch:
                        logger.warning("尝试进行最终修复：重建图结构...")
                        
                        try:
                            # 保存当前节点
                            combined_nodes = set(self.entity_node_keys + self.passage_node_keys)
                            graph_nodes = set(self.graph.vs["name"])
                            
                            # 删除并重建图
                            old_graph = self.graph.copy()
                            self.graph = ig.Graph(directed=self.global_config.is_directed_graph)
                            
                            # 添加所有节点
                            self.graph.add_vertices(len(combined_nodes), attributes={"name": list(combined_nodes)})
                            
                            # 尝试从旧图复制边
                            edges_to_add = []
                            edge_weights = []
                            
                            # 建立节点名称到索引的映射
                            name_to_idx = {name: idx for idx, name in enumerate(self.graph.vs["name"])}
                            
                            # 复制可能的边
                            for edge in old_graph.es:
                                source_name = old_graph.vs[edge.source]["name"]
                                target_name = old_graph.vs[edge.target]["name"]
                                
                                if source_name in name_to_idx and target_name in name_to_idx:
                                    edges_to_add.append((name_to_idx[source_name], name_to_idx[target_name]))
                                    edge_weights.append(edge["weight"] if "weight" in edge.attributes() else 1.0)
                            
                            # 添加边
                            if edges_to_add:
                                self.graph.add_edges(edges_to_add)
                                self.graph.es["weight"] = edge_weights
                            
                            logger.info(f"重建图结构完成: {self.graph.vcount()} 个节点, {self.graph.ecount()} 条边")
                            self.save_igraph()
                        except Exception as e:
                            logger.error(f"重建图结构失败: {str(e)}")
            else:
                logger.warning("图中没有'name'属性，无法进行自动修复")
        
        # 使用更安全的断言，避免中断整个程序
        try:
            # 检查唯一节点数是否与图顶点数匹配
            unique_nodes = len(set(self.entity_node_keys + self.passage_node_keys))
            assert unique_nodes == self.graph.vcount(), f"唯一节点数 {unique_nodes} != 图顶点数 {self.graph.vcount()}"
            logger.info("节点数量验证通过")
        except AssertionError as e:
            logger.error(f"节点数量验证失败: {str(e)}")
            logger.warning("继续执行，但可能会出现检索错误")

        # 构建节点名称到索引的映射
        igraph_name_to_idx = {}
        for idx, node in enumerate(self.graph.vs):
            if "name" in node.attributes():
                igraph_name_to_idx[node["name"]] = idx
        
        self.node_name_to_vertex_idx = igraph_name_to_idx
        
        # 安全地构建节点索引列表
        self.entity_node_idxs = []
        missing_entity_nodes = 0
        for node_key in self.entity_node_keys:
            if node_key in igraph_name_to_idx:
                self.entity_node_idxs.append(igraph_name_to_idx[node_key])
            else:
                missing_entity_nodes += 1
        
        self.passage_node_idxs = []
        missing_passage_nodes = 0
        for node_key in self.passage_node_keys:
            if node_key in igraph_name_to_idx:
                self.passage_node_idxs.append(igraph_name_to_idx[node_key])
            else:
                missing_passage_nodes += 1
        
        if missing_entity_nodes > 0 or missing_passage_nodes > 0:
            logger.warning(f"发现 {missing_entity_nodes} 个实体节点和 {missing_passage_nodes} 个段落节点在图中找不到")

        logger.info("Loading embeddings.")
        self.entity_embeddings = np.array(self.entity_embedding_store.get_embeddings(self.entity_node_keys))
        self.passage_embeddings = np.array(self.chunk_embedding_store.get_embeddings(self.passage_node_keys))

        self.fact_embeddings = np.array(self.fact_embedding_store.get_embeddings(self.fact_node_keys))
        logger.debug(f"事实嵌入形状: {self.fact_embeddings.shape}")

        all_openie_info, chunk_keys_to_process = self.load_existing_openie([])
        logger.debug(f"加载OpenIE信息: {len(all_openie_info)}条记录")

        self.proc_triples_to_docs = {}

        for doc in all_openie_info:
            triples = flatten_facts([doc['extracted_triples']])
            for triple in triples:
                if len(triple) == 3:
                    proc_triple = tuple(text_processing(list(triple)))
                    self.proc_triples_to_docs[str(proc_triple)] = self.proc_triples_to_docs.get(str(proc_triple), set()).union(set([doc['idx']]))
        
        logger.debug(f"处理后的三元组到文档映射数: {len(self.proc_triples_to_docs)}")
        # 打印一些映射示例
        if len(self.proc_triples_to_docs) > 0:
            sample_triples = list(self.proc_triples_to_docs.items())[:3]
            logger.debug(f"三元组到文档映射示例:")
            for triple, doc_ids in sample_triples:
                logger.debug(f"  {triple}: {doc_ids}")

        if self.ent_node_to_chunk_ids is None:
            logger.debug("初始化实体节点到文档块的映射")
            ner_results_dict, triple_results_dict = reformat_openie_results(all_openie_info)

            # 检查数据一致性
            if len(self.passage_node_keys) != len(ner_results_dict) or len(self.passage_node_keys) != len(triple_results_dict):
                logger.warning(f"段落节点数({len(self.passage_node_keys)})与NER结果数({len(ner_results_dict)})或三元组结果数({len(triple_results_dict)})不一致")

            # prepare data_store
            chunk_triples = []
            for chunk_id in self.passage_node_keys:
                if chunk_id in triple_results_dict:
                    chunk_triples.append([text_processing(t) for t in triple_results_dict[chunk_id].triples])
                else:
                    logger.warning(f"段落节点 {chunk_id} 在三元组结果中找不到")
                    chunk_triples.append([])

            self.node_to_node_stats = {}
            self.ent_node_to_chunk_ids = {}
            self.add_fact_edges(self.passage_node_keys, chunk_triples)
            logger.debug(f"实体节点到文档块映射数: {len(self.ent_node_to_chunk_ids)}")

        self.ready_to_retrieve = True
        logger.info("Ready for retrieval now.")

    def get_query_embeddings(self, queries: List[str] | List[QuerySolution]):
        """
        获取查询的向量表示

        为查询生成两种不同的向量表示：
        1. 用于查询-事实匹配的向量
        2. 用于查询-段落匹配的向量

        参数:
            queries (Union[List[str], List[QuerySolution]]): 
                查询列表，可以是字符串或QuerySolution对象
        """

        all_query_strings = []
        for query in queries:
            if isinstance(query, QuerySolution) and (
                    query.question not in self.query_to_embedding['triple'] or query.question not in
                    self.query_to_embedding['passage']):
                all_query_strings.append(query.question)
            elif query not in self.query_to_embedding['triple'] or query not in self.query_to_embedding['passage']:
                all_query_strings.append(query)

        if len(all_query_strings) > 0:
            # get all query embeddings
            logger.info(f"Encoding {len(all_query_strings)} queries for query_to_fact.")
            query_embeddings_for_triple = self.embedding_model.batch_encode(
                    all_query_strings,
                    instruction=get_query_instruction('query_to_fact'),
                    norm=True
                    )
            for query, embedding in zip(all_query_strings, query_embeddings_for_triple):
                self.query_to_embedding['triple'][query] = embedding

            logger.info(f"Encoding {len(all_query_strings)} queries for query_to_passage.")
            query_embeddings_for_passage = self.embedding_model.batch_encode(
                    all_query_strings,
                    instruction=get_query_instruction('query_to_passage'),
                    norm=True
                    )
            for query, embedding in zip(all_query_strings, query_embeddings_for_passage):
                self.query_to_embedding['passage'][query] = embedding

    def get_fact_scores(self, query: str) -> np.ndarray:
        """
        计算查询与事实的相似度得分

        使用向量相似度计算查询与所有事实的相关性得分，
        并进行归一化处理。

        参数:
            query (str): 用户查询

        返回:
            np.ndarray: 归一化后的查询-事实相似度得分数组
        """
        logger.debug(f"开始计算查询 '{query}' 与事实的相似度得分")
        logger.debug(f"事实嵌入数量: {len(self.fact_embeddings)}")
        
        query_embedding = self.query_to_embedding['triple'].get(query, None)
        if query_embedding is None:
            logger.debug(f"查询 '{query}' 的事实嵌入不存在，进行编码")
            query_embedding = self.embedding_model.batch_encode(
                    query,
                    instruction=get_query_instruction('query_to_fact'),
                    norm=True
                    )
        else:
            logger.debug(f"使用缓存的查询 '{query}' 的事实嵌入")

        query_fact_scores = np.dot(self.fact_embeddings, query_embedding.T) # shape: (#facts, )
        query_fact_scores = np.squeeze(query_fact_scores) if query_fact_scores.ndim == 2 else query_fact_scores
        query_fact_scores = min_max_normalize(query_fact_scores)
        
        logger.debug(f"事实得分计算完成，得分范围: {np.min(query_fact_scores)} 到 {np.max(query_fact_scores)}")
        return query_fact_scores

    def dense_passage_retrieval(self, query: str) -> Tuple[np.ndarray, np.ndarray]:
        """
        执行密集段落检索

        使用向量相似度在段落级别进行检索：
        1. 获取查询的向量表示
        2. 计算与所有段落的相似度
        3. 对得分进行归一化
        4. 返回排序结果

        参数:
            query (str): 用户查询

        返回:
            Tuple[np.ndarray, np.ndarray]:
            - 按相关性排序的文档ID数组
            - 对应的相似度得分数组
        """
        # 检查passage_embeddings是否为空
        if len(self.passage_embeddings) == 0:
            logger.info('No passage embeddings available in dense_passage_retrieval')
            return np.array([], dtype=int), np.array([], dtype=float)
            
        query_embedding = self.query_to_embedding['passage'].get(query, None)
        if query_embedding is None:
            query_embedding = self.embedding_model.batch_encode(
                    query,
                    instruction=get_query_instruction('query_to_passage'),
                    norm=True
                    )
        query_doc_scores = np.dot(self.passage_embeddings, query_embedding.T)
        query_doc_scores = np.squeeze(query_doc_scores) if query_doc_scores.ndim == 2 else query_doc_scores
        query_doc_scores = min_max_normalize(query_doc_scores)

        sorted_doc_ids = np.argsort(query_doc_scores)[::-1]
        sorted_doc_scores = query_doc_scores[sorted_doc_ids.tolist()]
        return sorted_doc_ids, sorted_doc_scores


    def get_top_k_weights(
            self,
            link_top_k: int,
            all_phrase_weights: np.ndarray,
            linking_score_map: Dict[str, float]
            ) -> Tuple[np.ndarray, Dict[str, float]]:
        """
        获取top-k个短语的权重

        从所有短语中选择得分最高的k个，并更新它们的权重：
        1. 根据得分对短语进行排序
        2. 选择前k个短语
        3. 将未选中短语的权重设为0

        参数:
            link_top_k (int): 需要选择的短语数量
            all_phrase_weights (np.ndarray): 所有短语的权重数组
            linking_score_map (Dict[str, float]): 短语到得分的映射

        返回:
            Tuple[np.ndarray, Dict[str, float]]:
            - 更新后的短语权重数组
            - 筛选后的得分映射
        """
        # choose top ranked nodes in linking_score_map
        linking_score_map = dict(sorted(linking_score_map.items(), key=lambda x: x[1], reverse=True)[:link_top_k])

        # only keep the top_k phrases in all_phrase_weights
        top_k_phrases = set(linking_score_map.keys())
        top_k_phrases_keys = set(
            [compute_mdhash_id(content=top_k_phrase, prefix="entity-") for top_k_phrase in top_k_phrases])

        # 首先将所有权重清零
        all_phrase_weights[:] = 0.0
        
        # 只为在linking_score_map中的短语设置权重
        actual_found_keys = set()
        for phrase in top_k_phrases:
            phrase_key = compute_mdhash_id(content=phrase, prefix="entity-")
            phrase_id = self.node_name_to_vertex_idx.get(phrase_key, None)
            if phrase_id is not None:
                all_phrase_weights[phrase_id] = linking_score_map[phrase]
                actual_found_keys.add(phrase)
        
        # 更新linking_score_map，只保留实际在图中找到的短语
        linking_score_map = {k: v for k, v in linking_score_map.items() if k in actual_found_keys}
        
        nonzero_count = np.count_nonzero(all_phrase_weights)
        map_keys_count = len(linking_score_map.keys())
        
        if nonzero_count != map_keys_count:
            logger.warning(f"调整后，非零权重数量 ({nonzero_count}) 与 linking_score_map 键数量 ({map_keys_count}) 仍不匹配")
        
        # 断言应该总是成功
        assert np.count_nonzero(all_phrase_weights) == len(linking_score_map.keys())
        
        return all_phrase_weights, linking_score_map

    def graph_search_with_fact_entities(
            self,
            query: str,
            link_top_k: int,
            query_fact_scores: np.ndarray,
            top_k_facts: List[Tuple],
            top_k_fact_indices: List[str],
            passage_node_weight: float = 0.05
            ) -> Tuple[np.ndarray, np.ndarray]:
        """
        基于事实实体的图搜索

        该方法结合了以下几个关键步骤：
        1. 基于查询相关的事实为实体和关系分配权重
        2. 执行密集段落检索获取初始文档得分
        3. 使用个性化PageRank算法在图上传播相关性
        4. 整合所有信息得到最终的文档排序

        参数:
            query (str): 用户查询
            link_top_k (int): 选择的top-k个短语数量
            query_fact_scores (np.ndarray): 查询与事实的相似度得分
            top_k_facts (List[Tuple]): 排名靠前的事实列表
            top_k_fact_indices (List[str]): 排名靠前的事实索引
            passage_node_weight (float): 段落节点的权重系数，默认0.05

        返回:
            Tuple[np.ndarray, np.ndarray]: 
            - 排序后的文档ID数组
            - 对应的相关性得分数组
        """
        logger.debug(f"开始图搜索过程，查询: {query}")
        logger.debug(f"处理的事实数量: {len(top_k_facts)}")
        logger.debug(f"处理的前5个事实: {top_k_facts[:5]}")
        logger.debug(f"对应的事实索引: {top_k_fact_indices[:5] if len(top_k_fact_indices) > 0 else '[]'}")

        #Assigning phrase weights based on selected facts from previous steps.
        linking_score_map = {}  # from phrase to the average scores of the facts that contain the phrase
        phrase_scores = {}  # store all fact scores for each phrase regardless of whether they exist in the knowledge graph or not
        phrase_weights = np.zeros(len(self.graph.vs['name']))
        passage_weights = np.zeros(len(self.graph.vs['name']))

        logger.debug("开始处理事实并分配短语权重...")
        for rank, f in enumerate(top_k_facts):
            subject_phrase = f[0].lower()
            predicate_phrase = f[1].lower()
            object_phrase = f[2].lower()
            logger.debug(f"处理事实: ({subject_phrase}, {predicate_phrase}, {object_phrase})")
            
            fact_score = query_fact_scores[
                top_k_fact_indices[rank]] if query_fact_scores.ndim > 0 else query_fact_scores
            
            logger.debug(f"处理事实 {rank+1}: {subject_phrase} - {predicate_phrase} - {object_phrase}")
            logger.debug(f"事实得分: {fact_score}")
            
            for phrase in [subject_phrase, object_phrase]:
                phrase_key = compute_mdhash_id(
                    content=phrase,
                    prefix="entity-"
                )
                logger.debug(f"计算短语 '{phrase}' 的哈希ID: {phrase_key}")
                
                phrase_id = self.node_name_to_vertex_idx.get(phrase_key, None)

                if phrase_id is not None:
                    phrase_weights[phrase_id] = fact_score
                    chunk_count = len(self.ent_node_to_chunk_ids.get(phrase_key, set()))
                    logger.debug(f"短语 '{phrase}' 在图中找到，ID: {phrase_id}，关联的文档块数: {chunk_count}")

                    if chunk_count > 0:
                        phrase_weights[phrase_id] /= chunk_count
                        logger.debug(f"调整后的短语权重: {phrase_weights[phrase_id]}")
                else:
                    logger.debug(f"短语 '{phrase}' 在图中未找到对应节点")
                    logger.debug(f"图中的部分节点名称: {list(self.node_name_to_vertex_idx.keys())[:5]}")

                if phrase not in phrase_scores:
                    phrase_scores[phrase] = []
                phrase_scores[phrase].append(fact_score)

        # calculate average fact score for each phrase
        logger.debug("计算每个短语的平均事实得分...")
        for phrase, scores in phrase_scores.items():
            linking_score_map[phrase] = float(np.mean(scores))
            logger.debug(f"短语 '{phrase}' 的平均得分: {linking_score_map[phrase]}")

        if link_top_k:
            logger.debug(f"筛选前 {link_top_k} 个最相关的短语...")
            phrase_weights, linking_score_map = self.get_top_k_weights(
                    link_top_k,
                    phrase_weights,
                    linking_score_map
                    )
            logger.debug(f"筛选后的短语数量: {len(linking_score_map)}")
            logger.debug(f"筛选后的短语: {list(linking_score_map.keys())[:5]}")

        #Get passage scores according to chosen dense retrieval model
        logger.debug("执行密集段落检索...")
        dpr_sorted_doc_ids, dpr_sorted_doc_scores = self.dense_passage_retrieval(query)
        normalized_dpr_sorted_scores = min_max_normalize(dpr_sorted_doc_scores)
        logger.debug(f"密集检索得到的文档数: {len(dpr_sorted_doc_ids)}")

        logger.debug("为段落节点分配权重...")
        for i, dpr_sorted_doc_id in enumerate(dpr_sorted_doc_ids.tolist()):
            passage_node_key = self.passage_node_keys[dpr_sorted_doc_id]
            passage_dpr_score = normalized_dpr_sorted_scores[i]
            passage_node_id = self.node_name_to_vertex_idx[passage_node_key]
            passage_weights[passage_node_id] = passage_dpr_score * passage_node_weight
            passage_node_text = self.chunk_embedding_store.get_row(passage_node_key)["content"]
            linking_score_map[passage_node_text] = passage_dpr_score * passage_node_weight
            
            if i < 5:  # 只打印前5个段落的详细信息
                logger.debug(f"段落 {i+1} (ID: {passage_node_id}):")
                logger.debug(f"  原始得分: {passage_dpr_score}")
                logger.debug(f"  加权后得分: {passage_weights[passage_node_id]}")
                logger.debug(f"  内容: {passage_node_text[:100]}...")

        #Combining phrase and passage scores into one array for PPR
        node_weights = phrase_weights + passage_weights
        logger.debug(f"合并后的节点权重统计:")
        logger.debug(f"  非零权重节点数: {np.count_nonzero(node_weights)}")
        logger.debug(f"  最大权重: {np.max(node_weights)}")
        logger.debug(f"  平均权重: {np.mean(node_weights[node_weights > 0])}")
        logger.debug(f"  短语非零权重节点数: {np.count_nonzero(phrase_weights)}")
        logger.debug(f"  段落非零权重节点数: {np.count_nonzero(passage_weights)}")

        #Recording top 30 facts in linking_score_map
        if len(linking_score_map) > 30:
            linking_score_map = dict(sorted(linking_score_map.items(), key=lambda x: x[1], reverse=True)[:30])
            logger.debug("保留前30个最高得分的链接...")
            logger.debug(f"保留后的链接: {list(linking_score_map.keys())[:5]}")

        # 检查是否找到了相关短语
        if sum(node_weights) <= 0:
            logger.debug(f'没有在图中找到与给定事实相关的短语: {top_k_facts}，使用密集段落检索结果')
            return dpr_sorted_doc_ids, dpr_sorted_doc_scores

        #Running PPR algorithm based on the passage and phrase weights previously assigned
        logger.debug("开始执行个性化PageRank算法...")
        ppr_start = time.time()
        ppr_sorted_doc_ids, ppr_sorted_doc_scores = self.run_ppr(node_weights, damping=self.global_config.damping)
        ppr_end = time.time()

        self.ppr_time += (ppr_end - ppr_start)
        logger.debug(f"PageRank计算完成，耗时: {ppr_end - ppr_start:.2f}秒")
        logger.debug(f"PageRank检索到的文档数: {len(ppr_sorted_doc_ids)}")
        if len(ppr_sorted_doc_ids) > 0:
            logger.debug(f"PageRank最高得分: {ppr_sorted_doc_scores[0]}")

        assert len(ppr_sorted_doc_ids) == len(
            self.passage_node_idxs), f"Doc prob length {len(ppr_sorted_doc_ids)} != corpus length {len(self.passage_node_idxs)}"

        return ppr_sorted_doc_ids, ppr_sorted_doc_scores

    def rerank_facts(self, query: str, query_fact_scores: np.ndarray) -> Tuple[List[int], List[Tuple], dict]:
        """
        对检索到的事实进行重排序

        使用识别记忆机制对初始检索的事实进行重排序，以提高其与查询的相关性。
        该方法会考虑事实的语义相似度和结构信息。

        参数:
            query (str): 用户查询
            query_fact_scores (np.ndarray): 查询与事实的初始相似度得分

        返回:
            Tuple[List[int], List[Tuple], dict]: 
            - 重排序后的事实索引列表
            - 重排序后的事实列表
            - 重排序过程的日志信息
        """
        # load args
        link_top_k: int = self.global_config.linking_top_k
        logger.debug(f"开始对查询 '{query}' 的事实进行重排序")
        logger.debug(f"事实得分形状: {query_fact_scores.shape}")

        candidate_fact_indices = np.argsort(query_fact_scores)[-link_top_k:][
                                 ::-1].tolist()  # list of ranked link_top_k fact relative indices
        real_candidate_fact_ids = [self.fact_node_keys[idx] for idx in
                                   candidate_fact_indices]  # list of ranked link_top_k fact keys
        
        logger.debug(f"候选事实索引: {candidate_fact_indices[:5]}{'...' if len(candidate_fact_indices) > 5 else ''}")
        logger.debug(f"候选事实ID: {real_candidate_fact_ids[:5]}{'...' if len(real_candidate_fact_ids) > 5 else ''}")
        
        fact_row_dict = self.fact_embedding_store.get_rows(real_candidate_fact_ids)
        candidate_facts = [eval(fact_row_dict[id]['content']) for id in real_candidate_fact_ids]  # list of link_top_k facts (each fact is a relation triple in tuple data type)
        
        logger.debug(f"重排序前的候选事实: {candidate_facts[:5]}{'...' if len(candidate_facts) > 5 else ''}")

        top_k_fact_indices, top_k_facts, reranker_dict = self.rerank_filter(
                query,
                candidate_facts,
                candidate_fact_indices,
                len_after_rerank=link_top_k
                )
        
        logger.debug(f"重排序后的事实: {top_k_facts[:5]}{'...' if len(top_k_facts) > 5 else ''}")
        logger.debug(f"重排序后的事实索引: {top_k_fact_indices[:5]}{'...' if len(top_k_fact_indices) > 5 else ''}")

        rerank_log = {'facts_before_rerank': candidate_facts, 'facts_after_rerank': top_k_facts}

        return top_k_fact_indices, top_k_facts, rerank_log
    
    def run_ppr(
            self,
            reset_prob: np.ndarray,
            damping: float =0.5
            ) -> Tuple[np.ndarray, np.ndarray]:
        """
        执行个性化PageRank (PPR)算法

        PPR算法通过图结构传播查询相关性，考虑节点间的连接关系。
        本实现与igraph的personalized_pagerank等价，无论是在标准模式还是数据库模式下。

        参数:
            reset_prob (np.ndarray): 重置概率分布，表示各节点的初始权重
            damping (float): 阻尼系数，控制随机游走与重置的平衡，默认0.5

        返回:
            Tuple[np.ndarray, np.ndarray]: 包含两个内容:
                1. 排序后的文档节点ID数组
                2. 对应的文档节点分数数组
        """
        ppr_start = time.time()
        logger.info(f"PPR方法被调用: damping={damping}, reset_prob形状={reset_prob.shape}")
        logger.info(f"reset_prob非零值数量: {np.count_nonzero(reset_prob)}")
        
        # 处理reset_prob中的NaN和负值
        reset_prob = np.where(np.isnan(reset_prob) | (reset_prob < 0), 0, reset_prob)
        
        # 确保prepare_retrieval_objects被调用
        if not hasattr(self, 'node_name_to_vertex_idx') or not hasattr(self, 'graph'):
            logger.warning("graph或node_name_to_vertex_idx尚未初始化，调用prepare_retrieval_objects")
            self.prepare_retrieval_objects()
        
        # 记录一些统计信息
        logger.info(f"图节点数: {self.graph.vcount()}, 图边数: {self.graph.ecount()}")
        logger.info(f"passage_node_idxs长度: {len(self.passage_node_idxs)}")
        
        # 使用完全相同的接口调用personalized_pagerank，无论是标准模式还是数据库模式
        # 由于我们已经在DirectDBGraph中实现了完全兼容的personalized_pagerank方法
        # 所以这里可以保持接口完全一致
        logger.info("开始执行personalized_pagerank算法...")
        try:
            pagerank_scores = self.graph.personalized_pagerank(
                vertices=range(len(self.node_name_to_vertex_idx)),
                damping=damping,
                directed=False,  # 始终使用无向模式，与原始实现一致
                weights='weight',
                reset=reset_prob,
                implementation='prpack'
            )
            logger.info(f"personalized_pagerank执行成功，得分形状: {pagerank_scores.shape if hasattr(pagerank_scores, 'shape') else '非数组'}")
        except Exception as e:
            logger.error(f"执行personalized_pagerank时出错: {str(e)}")
            # 创建一个空的结果数组
            pagerank_scores = np.zeros(self.graph.vcount())
        
        # 计算文档分数并排序
        doc_scores = np.array([pagerank_scores[idx] for idx in self.passage_node_idxs])
        sorted_doc_ids = np.argsort(doc_scores)[::-1]
        sorted_doc_scores = doc_scores[sorted_doc_ids.tolist()]
        
        ppr_end = time.time()
        logger.info(f"PPR完成，耗时: {ppr_end - ppr_start:.2f}秒，返回 {len(sorted_doc_ids)} 个排序后的文档")
        self.ppr_time += ppr_end - ppr_start
        
        return sorted_doc_ids, sorted_doc_scores

    def initialize_embeddings(self):
        """
        初始化向量存储

        设置文档和实体的向量存储：
        1. 初始化文档块向量存储
        2. 初始化实体向量存储
        3. 配置向量存储参数（维度、相似度计算方法等）
        """
        self.entity_embedding_store.insert_strings(self.entity_embedding_store.get_all_id_to_rows().values())
        self.fact_embedding_store.insert_strings(self.fact_embedding_store.get_all_id_to_rows().values())

    def process_chunks(self, chunks: List[dict]) -> None:
        """
        处理文档块

        对输入的文档块进行处理，包括：
        1. 实体识别和关系抽取
        2. 向量化和存储
        3. 更新知识图谱结构

        参数:
            chunks (List[dict]): 待处理的文档块列表，每个文档块包含内容和元数据
        """
        for chunk in chunks:
            passage = chunk['content']
            entities = self.openie.extract_entities(passage)
            triples = self.openie.extract_triples(passage)
            self.entity_embedding_store.insert_strings(entities)
            self.fact_embedding_store.insert_strings([str(triple) for triple in triples])
            self.add_fact_edges([chunk['id']], triples)
            self.add_passage_edges([chunk['id']], [entities])

    def process_query(self, query: str) -> Tuple[str, List[dict], List[dict]]:
        """
        处理查询

        对输入的查询进行处理，包括：
        1. 实体识别和关系抽取
        2. 向量化
        3. 检索相关文档和实体

        参数:
            query (str): 用户输入的查询文本

        返回:
            Tuple[str, List[dict], List[dict]]:
            - 处理后的查询文本
            - 相关文档列表
            - 相关实体列表
        """
        entities = self.openie.extract_entities(query)
        triples = self.openie.extract_triples(query)
        return query, self.entity_embedding_store.get_embeddings(entities), self.fact_embedding_store.get_embeddings(triples)

    def get_node_weights(self, query_embedding: np.ndarray, top_k: int = 5) -> Dict[str, float]:
        """
        计算节点权重

        基于查询向量计算图中节点的权重：
        1. 计算查询向量与节点向量的相似度
        2. 选择top-k个最相似的节点
        3. 归一化权重

        参数:
            query_embedding (np.ndarray): 查询文本的向量表示
            top_k (int): 返回的最相似节点数量

        返回:
            Dict[str, float]: 节点ID到权重的映射
        """
        similarities = np.dot(self.entity_embeddings, query_embedding.T)
        top_k_indices = similarities.argsort()[-top_k:][::-1]
        return {self.entity_embedding_store.get_id_to_row()[idx]['id']: similarities[idx] for idx in top_k_indices}

    def get_node_embeddings(self, node_ids: List[str]) -> Dict[str, np.ndarray]:
        """
        获取节点向量

        从向量存储中获取指定节点的向量表示：
        1. 区分实体节点和文档节点
        2. 从相应的向量存储中检索向量
        3. 处理缺失向量的情况

        参数:
            node_ids (List[str]): 需要获取向量的节点ID列表

        返回:
            Dict[str, np.ndarray]: 节点ID到向量的映射
        """
        return {node_id: self.entity_embedding_store.get_embedding(node_id) for node_id in node_ids}

    def reload_retrieval_objects(self):
        """
        手动重新加载检索对象
        
        当需要在不进行新索引的情况下重新加载所有检索相关的数据结构时，
        可以调用此方法。这在数据库发生变化但未通过正常索引流程更新时特别有用。
        
        这会强制重置检索就绪状态，并在下一次检索时重新加载所有数据。
        """
        logger.info("手动重置检索就绪状态")
        self.ready_to_retrieve = False
        logger.info("在下一次检索时将重新加载所有数据")
        
    def add_fact(self, subject: str, predicate: str, object: str, document_text: str = None):
        """
        手动添加事实三元组到系统中
        
        这个方法允许直接添加事实到系统，无需通过正常的OpenIE抽取流程。
        对于需要手动添加特定知识或修正系统自动抽取结果的情况非常有用。
        
        参数:
            subject (str): 事实的主语
            predicate (str): 事实的谓语
            object (str): 事实的宾语
            document_text (str, 可选): 包含此事实的文档内容。如果不提供，将自动生成一个包含该事实的文档。
            
        返回:
            bool: 添加成功返回True
        """
        logger.info(f"手动添加事实: ({subject}, {predicate}, {object})")
        
        # 如果没有提供文档，则创建一个包含该事实的简单文档
        if document_text is None:
            document_text = f"{subject} {predicate} {object}"
            
        # 创建三元组
        fact_tuple = (subject, predicate, object)
        
        # 添加文档到存储
        self.chunk_embedding_store.insert_strings([document_text])
        document_id = self.chunk_embedding_store.text_to_hash_id[document_text]
        
        # 添加实体
        self.entity_embedding_store.insert_strings([subject, object])
        subject_id = compute_mdhash_id(content=subject, prefix="entity-")
        object_id = compute_mdhash_id(content=object, prefix="entity-")
        
        # 添加事实
        fact_str = str(fact_tuple)
        self.fact_embedding_store.insert_strings([fact_str])
        
        # 建立关联
        if not hasattr(self, 'proc_triples_to_docs') or self.proc_triples_to_docs is None:
            self.proc_triples_to_docs = {}
            
        # 更新三元组到文档的映射
        proc_triple = tuple(text_processing(list(fact_tuple)))
        self.proc_triples_to_docs[str(proc_triple)] = self.proc_triples_to_docs.get(str(proc_triple), set()).union(set([document_id]))
        
        # 更新实体到文档块的映射
        if not hasattr(self, 'ent_node_to_chunk_ids') or self.ent_node_to_chunk_ids is None:
            self.ent_node_to_chunk_ids = {}
            
        self.ent_node_to_chunk_ids[subject_id] = self.ent_node_to_chunk_ids.get(subject_id, set()).union(set([document_id]))
        self.ent_node_to_chunk_ids[object_id] = self.ent_node_to_chunk_ids.get(object_id, set()).union(set([document_id]))
        
        # 更新节点间关系
        if not hasattr(self, 'node_to_node_stats') or self.node_to_node_stats is None:
            self.node_to_node_stats = {}
            
        self.node_to_node_stats[(subject_id, object_id)] = self.node_to_node_stats.get((subject_id, object_id), 0) + 1.0
        self.node_to_node_stats[(object_id, subject_id)] = self.node_to_node_stats.get((object_id, subject_id), 0) + 1.0
        
        # 将文档节点与实体节点连接
        self.node_to_node_stats[(document_id, subject_id)] = 1.0
        self.node_to_node_stats[(document_id, object_id)] = 1.0
        
        # 添加到图中
        # 先检查图是否需要添加新节点
        current_nodes = set(self.graph.vs["name"]) if "name" in self.graph.vs.attribute_names() else set()
        new_nodes = []
        
        for node_id in [subject_id, object_id, document_id]:
            if node_id not in current_nodes:
                new_nodes.append(node_id)
                
        if new_nodes:
            self.graph.add_vertices(len(new_nodes), attributes={"name": new_nodes})
            
        # 添加边
        for edge in [(subject_id, object_id), (object_id, subject_id), (document_id, subject_id), (document_id, object_id)]:
            if not self.graph.are_connected(edge[0], edge[1]):
                self.graph.add_edge(edge[0], edge[1], weight=1.0)
                
        # 保存图结构
        self.save_igraph()
        
        # 重置检索状态，确保下次查询时重新加载
        self.ready_to_retrieve = False
        
        logger.info(f"成功添加事实，并更新了图结构")
        return True

    def force_save_graph(self):
        """
        强制保存当前图数据，无论auto_save_graph设置如何。
        
        在禁用自动保存图(auto_save_graph=False)的情况下，
        应在完成一系列重要操作后调用此方法，
        以确保图数据被持久化保存。
        
        在直接数据库模式(direct_db_mode=True)下，此方法不执行任何操作，
        因为图数据直接存储在数据库中。
        """
        if self.direct_db_mode:
            logger.debug("在直接数据库模式下，图数据已经存储在数据库中，无需额外保存")
            return
            
        logger.info("强制保存图数据")
        self.save_igraph(force=True)

    def merge_duplicate_edges(self):
        """
        合并图中的重复边
        
        查找并合并具有相同源和目标节点的边，合并策略为累加权重。
        此方法可用于优化图结构，减少边的数量，提高PageRank计算效率。
        
        返回:
            Tuple[int, int]: (合并前边数, 合并后边数)
        """
        if not hasattr(self, 'graph') or self.graph is None or self.graph.ecount() == 0:
            logger.warning("图不存在或没有边，无需合并")
            return (0, 0)
            
        logger.info("开始合并重复边...")
        
        # 记录原始边数
        original_edge_count = self.graph.ecount()
        logger.info(f"合并前边数量: {original_edge_count}")
        
        # 创建(source_name, target_name) -> [(edge_index, weight), ...] 的映射
        edge_map = defaultdict(list)
        for edge_index, edge in enumerate(self.graph.es):
            source_name = self.graph.vs[edge.source]["name"]
            target_name = self.graph.vs[edge.target]["name"]
            weight = edge["weight"] if "weight" in edge.attributes() else 1.0
            edge_map[(source_name, target_name)].append((edge_index, weight))
        
        # 找出需要合并的边
        edges_to_delete = []  # 要删除的边索引
        edges_to_update = {}  # 格式: {edge_index: new_weight}
        
        duplicate_count = 0
        for edge_key, edges in edge_map.items():
            if len(edges) > 1:
                duplicate_count += len(edges) - 1
                # 保留第一条边，合并权重
                keep_edge_index = edges[0][0]
                total_weight = sum(weight for _, weight in edges)
                edges_to_update[keep_edge_index] = total_weight
                
                # 将其余边标记为删除
                for edge_index, _ in edges[1:]:
                    edges_to_delete.append(edge_index)
        
        logger.info(f"发现 {duplicate_count} 条重复边")
        
        # 更新保留的边的权重
        for edge_index, new_weight in edges_to_update.items():
            self.graph.es[edge_index]["weight"] = new_weight
            
        # 删除多余的边（从大到小删除，避免索引变化）
        if edges_to_delete:
            for edge_index in sorted(edges_to_delete, reverse=True):
                self.graph.delete_edges(edge_index)
            
        # 记录合并后边数
        new_edge_count = self.graph.ecount()
        logger.info(f"合并完成，删除了 {len(edges_to_delete)} 条重复边")
        logger.info(f"合并后边数量: {new_edge_count}")
        
        return (original_edge_count, new_edge_count)
        
    def optimize_graph(self):
        """
        优化图结构
        
        执行一系列优化操作以提高图的效率：
        1. 合并重复边
        2. 更新统计信息
        3. 保存优化后的图
        
        此方法应在大量新内容索引后调用，或作为定期维护任务执行。
        
        返回:
            Dict: 包含优化统计信息的字典
        """
        logger.info("开始优化图结构...")
        
        stats = {}
        
        # 区分直接数据库模式和标准模式
        if self.direct_db_mode:
            # 在直接数据库模式下，使用数据库对象的optimize_graph方法
            logger.info("使用数据库模式的图优化...")
            if hasattr(self.graph, 'optimize_graph'):
                stats = self.graph.optimize_graph()
            else:
                logger.warning("数据库图对象不支持optimize_graph方法")
                stats = {"error": "数据库图对象不支持optimize_graph方法"}
        else:
            # 在标准模式下，使用原有的合并重复边逻辑
            logger.info("使用标准模式的图优化...")
            # 合并重复边
            before_count, after_count = self.merge_duplicate_edges()
            stats["edge_reduction"] = before_count - after_count
            stats["edge_reduction_percent"] = round((before_count - after_count) / before_count * 100, 2) if before_count > 0 else 0
            
            # 记录图的基本信息
            stats["node_count"] = self.graph.vcount()
            stats["edge_count"] = self.graph.ecount()
            
            # 保存优化后的图
            self.save_igraph(force=True)
            
            logger.info(f"图优化完成: 节点数 {stats['node_count']}, 边数 {stats['edge_count']}")
            logger.info(f"减少了 {stats['edge_reduction']} 条边 ({stats['edge_reduction_percent']}%)")
        
        return stats