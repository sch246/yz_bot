import os, time
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Optional, Any
import igraph as ig
import numpy as np

from .logging_utils import get_logger
from .config_utils import BaseConfig

logger = get_logger(__name__)

class GraphDBConnector(ABC):
    """
    图数据库连接器的抽象基类。
    所有特定数据库的连接器都应该继承这个类并实现其方法。
    """
    
    def __init__(self, config: BaseConfig):
        """
        初始化图数据库连接器
        
        参数:
            config (BaseConfig): 包含连接信息的配置对象
        """
        self.config = config
        self.connected = False
        
    @abstractmethod
    def connect(self) -> bool:
        """
        连接到图数据库
        
        返回:
            bool: 是否成功连接
        """
        pass
    
    @abstractmethod
    def close(self) -> None:
        """关闭数据库连接"""
        pass
    
    @abstractmethod
    def save_graph(self, graph: ig.Graph) -> bool:
        """
        将igraph图对象保存到数据库
        
        参数:
            graph (ig.Graph): 要保存的图
            
        返回:
            bool: 操作是否成功
        """
        pass
    
    @abstractmethod
    def load_graph(self) -> Optional[ig.Graph]:
        """
        从数据库加载图
        
        返回:
            Optional[ig.Graph]: 加载的图对象，如果失败则返回None
        """
        pass
    
    @abstractmethod
    def clear_graph(self) -> bool:
        """
        清空数据库中的图
        
        返回:
            bool: 操作是否成功
        """
        pass
        
    @abstractmethod
    def add_nodes(self, nodes: List[Dict]) -> bool:
        """
        直接在数据库中添加节点，不使用igraph
        
        参数:
            nodes (List[Dict]): 要添加的节点列表，每个节点是一个包含属性的字典
        
        返回:
            bool: 操作是否成功
        """
        pass
    
    @abstractmethod
    def add_edges(self, edges: List[Dict]) -> bool:
        """
        直接在数据库中添加边，不使用igraph
        
        参数:
            edges (List[Dict]): 要添加的边列表，每个边是一个字典
        
        返回:
            bool: 操作是否成功
        """
        pass
    
    @abstractmethod
    def delete_nodes(self, node_names: List[str]) -> bool:
        """
        直接在数据库中删除节点，不使用igraph
        
        参数:
            node_names (List[str]): 要删除的节点名称列表
        
        返回:
            bool: 操作是否成功
        """
        pass
    
    @abstractmethod
    def are_connected(self, source_name: str, target_name: str) -> bool:
        """
        检查两个节点之间是否存在边
        
        参数:
            source_name (str): 源节点名称
            target_name (str): 目标节点名称
        
        返回:
            bool: 节点之间是否存在边
        """
        pass
    
    @abstractmethod
    def run_ppr(self, reset_nodes: List[str], reset_weights: List[float] = None, damping: float = 0.85, directed: bool = True, max_iterations: int = 20) -> Dict[str, float]:
        """
        直接在数据库中执行个性化PageRank算法
        
        参数:
            reset_nodes (List[str]): 重置概率分布节点列表
            reset_weights (List[float], optional): 对应reset_nodes的权重列表，如果为None则均匀分布
            damping (float): 阻尼系数，默认0.85
            directed (bool): 是否考虑图的有向性，默认True
            max_iterations (int): 最大迭代次数，默认20
        
        返回:
            Dict[str, float]: 节点名称到PageRank分数的映射
        """
        pass
    
    @abstractmethod
    def get_node_neighbors(self, node_name: str) -> List[str]:
        """
        获取节点的所有邻居节点
        
        参数:
            node_name (str): 节点名称
        
        返回:
            List[str]: 邻居节点名称列表
        """
        pass
    
    @abstractmethod
    def get_node_count(self) -> int:
        """
        获取图中节点数量
        
        返回:
            int: 节点数量
        """
        pass
    
    @abstractmethod
    def get_edge_count(self) -> int:
        """
        获取图中边的数量
        
        返回:
            int: 边数量
        """
        pass


class Neo4jConnector(GraphDBConnector):
    """
    Neo4j图数据库连接器实现
    """
    
    def __init__(self, config: BaseConfig):
        """初始化Neo4j连接器"""
        super().__init__(config)
        try:
            from neo4j import GraphDatabase
            self.GraphDatabase = GraphDatabase
        except ImportError:
            logger.error("需要安装neo4j包才能使用Neo4j连接器: pip install neo4j")
            raise
        
        self.driver = None
        # 添加PPR结果缓存，用于提高性能
        self._ppr_cache = {}
        # 添加最大缓存大小限制
        self._max_cache_size = 1000
        
    def connect(self) -> bool:
        """连接到Neo4j数据库"""
        try:
            uri = self.config.graph_db_url
            if not uri:
                # 构建URI
                host = "localhost"
                port = self.config.graph_db_port or 7687
                uri = f"neo4j://{host}:{port}"
                
            auth = (
                self.config.graph_db_username or "neo4j", 
                self.config.graph_db_password or "password"
            )
            
            self.driver = self.GraphDatabase.driver(uri, auth=auth)
            
            # 测试连接
            with self.driver.session() as session:
                session.run("RETURN 1")
                
            self.connected = True
            logger.info(f"成功连接到Neo4j数据库: {uri}")
            return True
            
        except Exception as e:
            logger.error(f"连接Neo4j数据库失败: {str(e)}")
            self.connected = False
            return False
    
    def close(self) -> None:
        """关闭Neo4j连接"""
        if self.driver:
            self.driver.close()
            self.connected = False
            logger.info("已关闭Neo4j数据库连接")
    
    def save_graph(self, graph: ig.Graph) -> bool:
        """将igraph图保存到Neo4j"""
        if not self.connected:
            if not self.connect():
                return False
        
        try:
            with self.driver.session() as session:
                # 清空现有数据
                session.run("MATCH (n) DETACH DELETE n")
                
                # 创建节点
                for v in graph.vs:
                    node_props = {k: v[k] for k in v.attribute_names()}
                    node_props_str = ", ".join([f"`{k}`: ${k}" for k in node_props.keys()])
                    
                    # 特殊处理'name'属性作为唯一标识符
                    if 'name' in node_props:
                        query = f"CREATE (n:Node {{name: $name, {node_props_str}}}) RETURN n"
                    else:
                        query = f"CREATE (n:Node {{{node_props_str}}}) RETURN n"
                        
                    session.run(query, **node_props)
                
                # 创建边
                for e in graph.es:
                    source_name = graph.vs[e.source]['name']
                    target_name = graph.vs[e.target]['name']
                    edge_props = {k: e[k] for k in e.attribute_names()}
                    
                    edge_props_str = ", ".join([f"`{k}`: ${k}" for k in edge_props.keys()])
                    
                    query = f"""
                    MATCH (a:Node {{name: $source_name}}), (b:Node {{name: $target_name}})
                    CREATE (a)-[r:RELATES_TO {{{edge_props_str}}}]->(b)
                    RETURN r
                    """
                    
                    session.run(query, source_name=source_name, target_name=target_name, **edge_props)
                
            logger.info(f"成功保存图到Neo4j: {graph.vcount()}个节点, {graph.ecount()}条边")
            return True
            
        except Exception as e:
            logger.error(f"保存图到Neo4j失败: {str(e)}")
            return False
    
    def load_graph(self) -> Optional[ig.Graph]:
        """从Neo4j加载图到igraph"""
        if not self.connected:
            if not self.connect():
                return None
        
        try:
            # 创建新图
            g = ig.Graph(directed=self.config.is_directed_graph)
            
            with self.driver.session() as session:
                # 获取所有节点
                result = session.run("MATCH (n:Node) RETURN n")
                nodes = []
                node_attrs = {}
                node_indices = {}
                
                for record in result:
                    node = record["n"]
                    node_id = node.get("name")
                    node_attrs_dict = dict(node.items())
                    
                    idx = len(nodes)
                    node_indices[node_id] = idx
                    nodes.append(node_id)
                    
                    # 收集属性
                    for key, value in node_attrs_dict.items():
                        if key not in node_attrs:
                            node_attrs[key] = [None] * len(nodes)
                        
                        # 确保列表长度与节点数量一致
                        if len(node_attrs[key]) < len(nodes):
                            node_attrs[key].extend([None] * (len(nodes) - len(node_attrs[key])))
                        
                        node_attrs[key][idx] = value
                
                # 添加节点和属性
                g.add_vertices(len(nodes))
                for attr_name, attr_values in node_attrs.items():
                    g.vs[attr_name] = attr_values
                
                # 获取所有边
                result = session.run("""
                MATCH (a:Node)-[r:RELATES_TO]->(b:Node)
                RETURN a.name AS source, b.name AS target, r
                """)
                
                edges = []
                edge_attrs = {}
                
                for record in result:
                    source_name = record["source"]
                    target_name = record["target"]
                    rel = record["r"]
                    
                    source_idx = node_indices[source_name]
                    target_idx = node_indices[target_name]
                    edges.append((source_idx, target_idx))
                    
                    # 收集边属性
                    rel_attrs = dict(rel.items())
                    edge_idx = len(edges) - 1
                    
                    for key, value in rel_attrs.items():
                        if key not in edge_attrs:
                            edge_attrs[key] = [None] * len(edges)
                        
                        # 确保列表长度与边数量一致
                        if len(edge_attrs[key]) < len(edges):
                            edge_attrs[key].extend([None] * (len(edges) - len(edge_attrs[key])))
                        
                        edge_attrs[key][edge_idx] = value
                
                # 添加边和属性
                g.add_edges(edges)
                for attr_name, attr_values in edge_attrs.items():
                    g.es[attr_name] = attr_values
            
            logger.info(f"从Neo4j成功加载图: {g.vcount()}个节点, {g.ecount()}条边")
            return g
            
        except Exception as e:
            logger.error(f"从Neo4j加载图失败: {str(e)}")
            return None
    
    def clear_graph(self) -> bool:
        """清空Neo4j数据库中的图"""
        if not self.connected:
            if not self.connect():
                return False
        
        try:
            with self.driver.session() as session:
                session.run("MATCH (n) DETACH DELETE n")
            
            logger.info("已清空Neo4j数据库中的图")
            return True
            
        except Exception as e:
            logger.error(f"清空Neo4j数据库中的图失败: {str(e)}")
            return False

    def add_nodes(self, nodes: List[Dict]) -> bool:
        """
        直接在Neo4j中添加节点，不使用igraph
        
        参数:
            nodes (List[Dict]): 要添加的节点列表，每个节点是一个包含属性的字典，必须有'name'字段
        
        返回:
            bool: 操作是否成功
        """
        if not self.connected:
            if not self.connect():
                return False
                
        try:
            with self.driver.session() as session:
                for node in nodes:
                    if 'name' not in node:
                        logger.error("节点必须包含'name'属性")
                        continue
                        
                    # 构建属性字符串
                    props = {k: v for k, v in node.items()}
                    props_str = ", ".join([f"`{k}`: ${k}" for k in props.keys()])
                    
                    # 使用MERGE而不是CREATE，避免创建重复节点
                    query = f"""
                    MERGE (n:Node {{name: $name}})
                    SET n = {{{props_str}}}
                    RETURN n
                    """
                    
                    session.run(query, **props)
                    
            logger.info(f"成功添加 {len(nodes)} 个节点到Neo4j")
            return True
            
        except Exception as e:
            logger.error(f"添加节点到Neo4j失败: {str(e)}")
            return False
    
    def add_edges(self, edges: List[Dict]) -> bool:
        """
        直接在Neo4j中添加边，不使用igraph
        
        参数:
            edges (List[Dict]): 要添加的边列表，每个边是一个字典
                必须包含'source'和'target'字段(节点名称)，以及可选的属性
        
        返回:
            bool: 操作是否成功
        """
        if not self.connected:
            if not self.connect():
                return False
                
        try:
            with self.driver.session() as session:
                for edge in edges:
                    if 'source' not in edge or 'target' not in edge:
                        logger.error("边必须包含'source'和'target'属性")
                        continue
                        
                    source = edge.pop('source')
                    target = edge.pop('target')
                    
                    # 构建属性字符串
                    props_str = ", ".join([f"`{k}`: ${k}" for k in edge.keys()])
                    props_part = f"{{{props_str}}}" if props_str else ""
                    
                    query = f"""
                    MATCH (a:Node {{name: $source}}), (b:Node {{name: $target}})
                    MERGE (a)-[r:RELATES_TO {props_part}]->(b)
                    RETURN r
                    """
                    
                    session.run(query, source=source, target=target, **edge)
                    
            logger.info(f"成功添加 {len(edges)} 条边到Neo4j")
            return True
            
        except Exception as e:
            logger.error(f"添加边到Neo4j失败: {str(e)}")
            return False
    
    def delete_nodes(self, node_names: List[str]) -> bool:
        """
        直接在Neo4j中删除节点，不使用igraph
        
        参数:
            node_names (List[str]): 要删除的节点名称列表
        
        返回:
            bool: 操作是否成功
        """
        if not self.connected:
            if not self.connect():
                return False
                
        try:
            with self.driver.session() as session:
                for name in node_names:
                    query = """
                    MATCH (n:Node {name: $name})
                    DETACH DELETE n
                    """
                    
                    session.run(query, name=name)
                    
            logger.info(f"成功从Neo4j删除 {len(node_names)} 个节点")
            return True
            
        except Exception as e:
            logger.error(f"从Neo4j删除节点失败: {str(e)}")
            return False
    
    def are_connected(self, source_name: str, target_name: str) -> bool:
        """
        检查两个节点之间是否存在边
        
        参数:
            source_name (str): 源节点名称
            target_name (str): 目标节点名称
        
        返回:
            bool: 节点之间是否存在边
        """
        if not self.connected:
            if not self.connect():
                return False
                
        try:
            with self.driver.session() as session:
                query = """
                MATCH (a:Node {name: $source})-[r:RELATES_TO]->(b:Node {name: $target})
                RETURN COUNT(r) > 0 AS connected
                """
                
                result = session.run(query, source=source_name, target=target_name)
                record = result.single()
                return record and record["connected"]
                
        except Exception as e:
            logger.error(f"检查节点连接失败: {str(e)}")
            return False
    
    def run_ppr(self, reset_nodes: List[str], reset_weights: List[float] = None, damping: float = 0.85, directed: bool = True, max_iterations: int = 20) -> Dict[str, float]:
        """
        直接在Neo4j中执行个性化PageRank算法
        
        参数:
            reset_nodes (List[str]): 重置概率分布节点列表
            reset_weights (List[float], optional): 对应reset_nodes的权重列表，如果为None则均匀分布
            damping (float): 阻尼系数，默认0.85
            directed (bool): 是否考虑图的有向性，默认True
            max_iterations (int): 最大迭代次数，默认20
        
        返回:
            Dict[str, float]: 节点名称到PageRank分数的映射
        """
        logger.info(f"PPR调用开始 - 节点数: {len(reset_nodes)}, 最大迭代: {max_iterations}, 有向图: {directed}")
        if not self.connected:
            if not self.connect():
                return {}
        
        # 过滤掉权重为0的节点，减少计算量
        if reset_weights is not None:
            # 只保留权重>0的节点
            filtered_nodes_weights = [(n, w) for n, w in zip(reset_nodes, reset_weights) if w > 0]
            if filtered_nodes_weights:
                reset_nodes = [n for n, _ in filtered_nodes_weights]
                reset_weights = [w for _, w in filtered_nodes_weights]
            else:
                return {}  # 如果没有有效权重，直接返回空结果
        
        # 如果没有提供权重，则使用均匀权重
        if reset_weights is None:
            if len(reset_nodes) > 0:
                weight = 1.0 / len(reset_nodes)
                reset_weights = [weight] * len(reset_nodes)
            else:
                reset_weights = []
                
        # 确保权重和为1
        if reset_weights and sum(reset_weights) > 0:
            total_weight = sum(reset_weights)
            reset_weights = [w/total_weight for w in reset_weights]
            
        # 生成缓存键 - 使用哈希值减小键大小
        if len(reset_nodes) > 100:
            # 对于大量节点，使用哈希值作为缓存键一部分
            nodes_hash = hash(tuple(sorted(reset_nodes)))
            weights_hash = hash(tuple([round(w, 6) for w in reset_weights]))
            cache_key = (
                nodes_hash,
                weights_hash,
                damping,
                directed,
                max_iterations
            )
        else:
            # 对于少量节点，使用完整节点列表
            sorted_node_weights = sorted(zip(reset_nodes, reset_weights), key=lambda x: x[0])
            cache_key = (
                tuple((n, round(w, 6)) for n, w in sorted_node_weights),
                damping,
                directed,
                max_iterations
            )
        
        # 检查缓存
        if cache_key in self._ppr_cache:
            logger.info("命中缓存: 使用缓存的PageRank结果")
            return self._ppr_cache[cache_key]
        
        logger.info("缓存未命中: 执行PageRank计算")
                
        try:
            # 检查是否安装了图算法库
            with self.driver.session() as session:
                try:
                    # 优化：使用Neo4j Graph Data Science (GDS)库执行PageRank
                    # 这通常比Cypher实现快10-100倍
                    logger.info("尝试使用Neo4j Graph Data Science库执行PageRank...")
                    projection_name = f"hipporag_graph_{abs(hash(time.time())) % 10000}"  # 使用基于时间的唯一名称
                    
                    # 准备source_nodes参数 - 只包含有效节点
                    source_nodes_param = []
                    for i, node in enumerate(reset_nodes):
                        weight = reset_weights[i] if i < len(reset_weights) else 0.0
                        if weight > 0:  # 只添加权重大于0的节点
                            source_nodes_param.append({"name": node, "weight": weight})
                    
                    logger.debug(f"PPR源节点数量: {len(source_nodes_param)}")
                    
                    # 使用改进的GDS流程 - 一次性执行所有操作并使用批处理
                    # 使用streamRelationshipProperty进一步提高性能
                    query = f"""
                    CALL {{
                        // 创建子图投影 - 只包含必要的节点和关系
                        CALL gds.graph.project.cypher(
                            '{projection_name}',
                            'MATCH (n:Node) RETURN id(n) AS id, n.name AS name',
                            'MATCH (s:Node)-[r:RELATES_TO]->(t:Node) RETURN id(s) AS source, id(t) AS target, r.weight AS weight',
                            {{
                                nodeProperties: ['name'],
                                relationshipProperties: ['weight']
                            }}
                        )
                        YIELD graphName
                        
                        // 使用批处理参数执行PageRank
                        CALL gds.pageRank.stream('{projection_name}', {{
                            maxIterations: $max_iterations,
                            dampingFactor: $damping,
                            sourceNodes: $source_nodes,
                            relationshipWeightProperty: 'weight',
                            scaler: 'L1Norm',
                            concurrency: 4, // 使用并行处理
                            tolerance: 1e-4 // 提前收敛条件
                        }})
                        YIELD nodeId, score
                        
                        // 使用LIMIT减少返回结果数量 - 通常只需要少量最相关的节点
                        WITH gds.util.asNode(nodeId).name AS name, score
                        ORDER BY score DESC
                        LIMIT 1000  // 只返回最相关的1000个节点以提高性能
                        
                        RETURN name, score
                    }}
                    
                    // 删除投影以释放内存
                    CALL gds.graph.drop('{projection_name}', false)
                    YIELD graphName
                    
                    RETURN name, score
                    """
                    
                    start_time = time.time()
                    result = session.run(
                        query, 
                        max_iterations=max_iterations,
                        damping=damping,
                        source_nodes=source_nodes_param
                    )
                    
                    ranks = {}
                    count = 0
                    for record in result:
                        ranks[record["name"]] = record["score"]
                        count += 1
                    
                    end_time = time.time()
                    logger.info(f"Graph Data Science库PageRank计算完成，计算得到 {count} 个结果，耗时: {end_time - start_time:.2f}秒")
                    
                    # 保存到缓存 - 使用LRU缓存策略
                    if len(self._ppr_cache) >= self._max_cache_size:
                        # 如果缓存已满，移除最早的项
                        self._ppr_cache.pop(next(iter(self._ppr_cache)))
                    self._ppr_cache[cache_key] = ranks
                    
                    return ranks
                    
                except Exception as e:
                    logger.warning(f"使用Graph Data Science库执行PageRank失败: {str(e)}")
                    logger.warning("回退到优化的Cypher实现")
                    ranks = self._run_optimized_cypher_ppr(reset_nodes, reset_weights, damping, directed, max_iterations)
                    
                    # 保存到缓存
                    if len(self._ppr_cache) >= self._max_cache_size:
                        self._ppr_cache.pop(next(iter(self._ppr_cache)))
                    self._ppr_cache[cache_key] = ranks
                    
                    return ranks
                
        except Exception as e:
            logger.error(f"执行PageRank算法失败: {str(e)}")
            # 回退到优化的Cypher实现
            return self._run_optimized_cypher_ppr(reset_nodes, reset_weights, damping, directed, max_iterations)
    
    def _run_optimized_cypher_ppr(self, reset_nodes: List[str], reset_weights: List[float] = None, damping: float = 0.85, directed: bool = True, max_iterations: int = 20) -> Dict[str, float]:
        """
        使用优化的Cypher实现的PageRank算法
        
        参数:
            reset_nodes (List[str]): 重置概率分布节点列表
            reset_weights (List[float], optional): 对应reset_nodes的权重列表，如果为None则均匀分布
            damping (float): 阻尼系数，默认0.85
            directed (bool): 是否考虑图的有向性，默认True
            max_iterations (int): 最大迭代次数，默认20
        """
        import time
        start_time = time.time()
        logger.info(f"开始优化版Cypher PPR - 节点数: {len(reset_nodes)}, 最大迭代: {max_iterations}")
        
        try:
            # 过滤掉权重为0的节点
            if reset_weights is not None:
                filtered_nodes_weights = [(n, w) for n, w in zip(reset_nodes, reset_weights) if w > 0]
                if filtered_nodes_weights:
                    reset_nodes = [n for n, _ in filtered_nodes_weights]
                    reset_weights = [w for _, w in filtered_nodes_weights]
                else:
                    return {}
                    
            # 如果没有提供权重，则使用均匀权重
            if reset_weights is None:
                if len(reset_nodes) > 0:
                    weight = 1.0 / len(reset_nodes)
                    reset_weights = [weight] * len(reset_nodes)
                else:
                    reset_weights = []
            
            # 确保权重和为1
            if reset_weights and sum(reset_weights) > 0:
                total_weight = sum(reset_weights)
                reset_weights = [w/total_weight for w in reset_weights]
            
            # 创建节点名称到权重的映射
            reset_map = {}
            for i, node in enumerate(reset_nodes):
                if i < len(reset_weights):
                    reset_map[node] = reset_weights[i]
            
            logger.info(f"PPR源节点映射创建完成: {len(reset_map)} 个节点")
            
            # 预处理：获取节点总数，用于性能优化
            with self.driver.session() as session:
                # 优化：只加载需要的数据而不是全图
                # 1. 找出从reset_nodes可达的节点集合（通常比全图小得多）
                # 2. 只对这些节点进行PageRank计算
                
                logger.info("获取从源节点可达的子图...")
                t1 = time.time()
                
                # 使用参数化查询获取可达节点
                # 限制路径长度以提高性能，大多数有用的节点通常在3-4跳之内
                reachable_query = """
                MATCH path = (src:Node)-[*1..3]->(n:Node)
                WHERE src.name IN $reset_nodes
                RETURN DISTINCT n.name AS name
                UNION
                MATCH (src:Node)
                WHERE src.name IN $reset_nodes
                RETURN src.name AS name
                """
                
                reachable_result = session.run(reachable_query, reset_nodes=reset_nodes)
                reachable_nodes = set([record["name"] for record in reachable_result])
                
                t2 = time.time()
                logger.info(f"找到 {len(reachable_nodes)} 个可达节点，耗时: {t2-t1:.2f}秒")
                
                # 如果可达节点太多，采用随机抽样策略
                if len(reachable_nodes) > 10000:
                    logger.info(f"可达节点数量 ({len(reachable_nodes)}) 太大，进行随机抽样")
                    import random
                    # 始终保留源节点
                    sampled_nodes = set(reset_nodes)
                    # 随机抽样其他节点
                    other_nodes = list(reachable_nodes - sampled_nodes)
                    random.shuffle(other_nodes)
                    sampled_nodes.update(other_nodes[:10000-len(sampled_nodes)])
                    reachable_nodes = sampled_nodes
                    logger.info(f"抽样后节点数量: {len(reachable_nodes)}")
                
                # 获取子图的边结构
                logger.info("获取子图的边结构...")
                t1 = time.time()
                
                edges_query = """
                MATCH (src:Node)-[r:RELATES_TO]->(tgt:Node)
                WHERE src.name IN $nodes AND tgt.name IN $nodes
                RETURN src.name AS source, tgt.name AS target, r.weight AS weight
                """
                
                if not directed:
                    edges_query = """
                    MATCH (src:Node)-[r:RELATES_TO]-(tgt:Node)
                    WHERE src.name IN $nodes AND tgt.name IN $nodes
                    RETURN src.name AS source, tgt.name AS target, r.weight AS weight
                    """
                
                edges_result = session.run(edges_query, nodes=list(reachable_nodes))
                
                # 建立高效的数据结构用于PageRank计算
                adjacency_list = {}
                edge_weights = {}
                outgoing_sum = {}
                edge_count = 0
                
                for record in edges_result:
                    source = record["source"]
                    target = record["target"]
                    weight = record["weight"] if record["weight"] is not None else 1.0
                    edge_count += 1
                    
                    if source not in adjacency_list:
                        adjacency_list[source] = []
                    adjacency_list[source].append(target)
                    
                    edge_key = (source, target)
                    edge_weights[edge_key] = weight
                    
                    if source not in outgoing_sum:
                        outgoing_sum[source] = 0
                    outgoing_sum[source] += weight
                t2 = time.time()
                logger.info(f"获取和处理了 {edge_count} 条边，耗时: {t2-t1:.2f}秒")
                
                # 准备节点和PageRank数组 - 只包含可达节点
                all_node_names = list(reachable_nodes)
                name_to_idx = {name: idx for idx, name in enumerate(all_node_names)}
                
                # 使用NumPy数组加速计算
                pagerank_scores = np.zeros(len(all_node_names))
                next_pagerank_scores = np.zeros(len(all_node_names))
                
                # 初始化PageRank值
                for name, weight in reset_map.items():
                    if name in name_to_idx:
                        pagerank_scores[name_to_idx[name]] = weight
                
                # 迭代计算PageRank - 使用NumPy向量化操作
                logger.info(f"开始PageRank迭代计算 (最大{max_iterations}次)...")
                t1 = time.time()
                
                for iteration in range(max_iterations):
                    # 重置下一轮的PageRank值 - 向量化操作
                    next_pagerank_scores.fill(0.0)
                    
                    # 为所有重置节点添加基础值
                    for name, weight in reset_map.items():
                        if name in name_to_idx:
                            next_pagerank_scores[name_to_idx[name]] = weight * (1.0 - damping)
                    
                    # 计算贡献 - 通过邻接列表遍历
                    for node_name in adjacency_list:
                        if node_name in name_to_idx:
                            node_idx = name_to_idx[node_name]
                            
                            # 只有当节点有PageRank值时才贡献
                            if pagerank_scores[node_idx] > 0:
                                source_rank = pagerank_scores[node_idx]
                                neighbors = adjacency_list[node_name]
                                
                                # 计算当前节点的出边权重总和
                                total_weight = outgoing_sum.get(node_name, 0.0)
                                if total_weight > 0:
                                    for neighbor in neighbors:
                                        if neighbor in name_to_idx:
                                            neighbor_idx = name_to_idx[neighbor]
                                            edge_weight = edge_weights.get((node_name, neighbor), 1.0)
                                            
                                            # 计算贡献并添加
                                            contribution = source_rank * damping * (edge_weight / total_weight)
                                            next_pagerank_scores[neighbor_idx] += contribution
                    
                    # 检查收敛 - 如果变化很小，提前停止
                    diff = np.sum(np.abs(pagerank_scores - next_pagerank_scores))
                    
                    # 更新当前PageRank值 - 使用向量复制
                    np.copyto(pagerank_scores, next_pagerank_scores)
                    
                    if iteration % 5 == 0 or diff < 1e-4:
                        logger.debug(f"完成PageRank迭代 {iteration+1}/{max_iterations}，差异: {diff:.6f}")
                        
                    # 如果收敛，提前停止
                    if diff < 1e-4:
                        logger.info(f"PageRank在第 {iteration+1} 次迭代后收敛，差异: {diff:.6f}")
                        break
                
                t2 = time.time()
                logger.info(f"PageRank迭代计算完成，耗时: {t2-t1:.2f}秒")
                
                # 转换结果格式 - 只返回有意义的分数
                ranks = {}
                for idx, score in enumerate(pagerank_scores):
                    if score > 1e-5:  # 忽略极小值
                        ranks[all_node_names[idx]] = float(score)
                
                result_count = len(ranks)
                
                # 归一化结果
                if ranks:
                    total = sum(ranks.values())
                    if total > 0:
                        ranks = {k: v/total for k, v in ranks.items()}
                
                end_time = time.time()
                logger.info(f"优化的Cypher PageRank计算完成，得到 {result_count} 个结果，总耗时: {end_time - start_time:.2f}秒")
                return ranks
                
        except Exception as e:
            end_time = time.time()
            logger.error(f"执行优化的Cypher PageRank算法失败 ({end_time - start_time:.2f}秒): {str(e)}")
            logger.error(f"错误详情: {type(e).__name__}")
            import traceback
            logger.error(traceback.format_exc())
            return {}
            
    def _run_cypher_ppr(self, reset_nodes: List[str], reset_weights: List[float] = None, damping: float = 0.85, directed: bool = True, max_iterations: int = 20) -> Dict[str, float]:
        """
        原始的Cypher实现的PageRank算法，性能较差（已弃用，保留以便回退）
        """
        logger.warning("正在使用性能较差的原始Cypher PageRank实现！应使用优化版本代替。")
    
    def get_node_neighbors(self, node_name: str) -> List[str]:
        """
        获取节点的所有邻居节点
        
        参数:
            node_name (str): 节点名称
        
        返回:
            List[str]: 邻居节点名称列表
        """
        if not self.connected:
            if not self.connect():
                return []
                
        try:
            with self.driver.session() as session:
                query = """
                MATCH (n:Node {name: $name})-[r:RELATES_TO]-(m:Node)
                RETURN m.name AS neighbor
                """
                
                result = session.run(query, name=node_name)
                return [record["neighbor"] for record in result]
                
        except Exception as e:
            logger.error(f"获取节点邻居失败: {str(e)}")
            return []
    
    def get_node_count(self) -> int:
        """
        获取图中节点数量
        
        返回:
            int: 节点数量
        """
        if not self.connected:
            if not self.connect():
                return 0
                
        try:
            with self.driver.session() as session:
                result = session.run("MATCH (n:Node) RETURN COUNT(n) AS count")
                record = result.single()
                return record["count"] if record else 0
                
        except Exception as e:
            logger.error(f"获取节点数量失败: {str(e)}")
            return 0
    
    def get_edge_count(self) -> int:
        """
        获取图中边的数量
        
        返回:
            int: 边数量
        """
        if not self.connected:
            if not self.connect():
                return 0
                
        try:
            with self.driver.session() as session:
                result = session.run("MATCH ()-[r:RELATES_TO]->() RETURN COUNT(r) AS count")
                record = result.single()
                return record["count"] if record else 0
                
        except Exception as e:
            logger.error(f"获取边数量失败: {str(e)}")
            return 0


def get_graph_db_connector(config: BaseConfig) -> Optional[GraphDBConnector]:
    """
    根据配置获取适当的图数据库连接器
    
    参数:
        config (BaseConfig): 配置对象
        
    返回:
        Optional[GraphDBConnector]: 图数据库连接器实例，如果不使用图数据库则返回None
    """
    if not config.use_graph_db:
        return None
    
    graph_db_type = config.graph_db_type.lower()
    
    if graph_db_type == "neo4j":
        return Neo4jConnector(config)
    elif graph_db_type == "tigergraph":
        # TODO: 实现TigerGraph连接器
        logger.error("TigerGraph连接器尚未实现")
        return None
    elif graph_db_type == "neptune":
        # TODO: 实现Amazon Neptune连接器
        logger.error("Amazon Neptune连接器尚未实现")
        return None
    elif graph_db_type == "arangodb":
        # TODO: 实现ArangoDB连接器
        logger.error("ArangoDB连接器尚未实现")
        return None
    elif graph_db_type == "orientdb":
        # TODO: 实现OrientDB连接器
        logger.error("OrientDB连接器尚未实现")
        return None
    else:
        logger.error(f"不支持的图数据库类型: {graph_db_type}")
        return None


class DirectDBGraph:
    """
    直接使用数据库操作的图实现，作为igraph.Graph的替代品。
    这个类模拟igraph.Graph的部分接口，但所有操作都直接在数据库中执行，
    不需要将图完全加载到内存中。
    """
    
    def __init__(self, graph_db: GraphDBConnector, directed: bool = True):
        """
        初始化直接数据库图
        
        参数:
            graph_db (GraphDBConnector): 图数据库连接器
            directed (bool): 图是否有向，默认为True
        """
        self.graph_db = graph_db
        self.directed = directed
        
        # 确保连接到数据库
        if not self.graph_db.connected:
            self.graph_db.connect()
    
    def vcount(self) -> int:
        """
        获取图中节点数量
        
        返回:
            int: 节点数量
        """
        return self.graph_db.get_node_count()
    
    def ecount(self) -> int:
        """
        获取图中边的数量
        
        返回:
            int: 边数量
        """
        return self.graph_db.get_edge_count()
    
    def add_vertices(self, n: int, attributes: Dict[str, List] = None) -> None:
        """
        添加节点到图中
        
        参数:
            n (int): 要添加的节点数量
            attributes (Dict[str, List]): 节点属性字典，键为属性名，值为属性值列表
        """
        if not attributes or 'name' not in attributes:
            logger.error("添加节点时必须提供'name'属性")
            return
            
        nodes = []
        for i in range(n):
            node = {'name': attributes['name'][i]}
            
            # 添加其他属性
            for attr_name, attr_values in attributes.items():
                if attr_name != 'name' and i < len(attr_values):
                    node[attr_name] = attr_values[i]
                    
            nodes.append(node)
            
        self.graph_db.add_nodes(nodes)
    
    def add_edges(self, edges: List[Tuple], attributes: Dict[str, List] = None) -> None:
        """
        添加边到图中
        
        参数:
            edges (List[Tuple]): 边列表，每个边是一个(source_idx, target_idx)元组
            attributes (Dict[str, List]): 边属性字典，键为属性名，值为属性值列表
        """
        if not hasattr(self, 'vs') or not hasattr(self.vs, 'name'):
            logger.error("图中没有节点名称信息，无法添加边")
            return
            
        # 获取节点名称
        node_names = self.vs['name']
        
        db_edges = []
        for i, (source_idx, target_idx) in enumerate(edges):
            if source_idx >= len(node_names) or target_idx >= len(node_names):
                logger.error(f"边索引超出范围: ({source_idx}, {target_idx})")
                continue
                
            edge = {
                'source': node_names[source_idx],
                'target': node_names[target_idx]
            }
            
            # 添加边属性
            if attributes:
                for attr_name, attr_values in attributes.items():
                    if i < len(attr_values):
                        edge[attr_name] = attr_values[i]
                        
            db_edges.append(edge)
            
        self.graph_db.add_edges(db_edges)
    
    def delete_vertices(self, vertices: List[int] | List[str]) -> None:
        """
        删除节点
        
        参数:
            vertices (List[int] | List[str]): 要删除的节点索引或名称列表
        """
        if not vertices:
            return
            
        # 如果是整数索引，转换为节点名称
        if isinstance(vertices[0], int):
            if not hasattr(self, 'vs') or not hasattr(self.vs, 'name'):
                logger.error("图中没有节点名称信息，无法删除节点")
                return
                
            node_names = self.vs['name']
            node_names_to_delete = [node_names[idx] for idx in vertices if idx < len(node_names)]
        else:
            # 已经是节点名称
            node_names_to_delete = vertices
            
        self.graph_db.delete_nodes(node_names_to_delete)
    
    def are_connected(self, source: str | int, target: str | int) -> bool:
        """
        检查两个节点之间是否存在边
        
        参数:
            source (str | int): 源节点名称或索引
            target (str | int): 目标节点名称或索引
            
        返回:
            bool: 节点之间是否存在边
        """
        # 如果是整数索引，转换为节点名称
        if isinstance(source, int) or isinstance(target, int):
            if not hasattr(self, 'vs') or not hasattr(self.vs, 'name'):
                logger.error("图中没有节点名称信息，无法检查连接")
                return False
                
            node_names = self.vs['name']
            
            if isinstance(source, int):
                if source >= len(node_names):
                    return False
                source = node_names[source]
                
            if isinstance(target, int):
                if target >= len(node_names):
                    return False
                target = node_names[target]
                
        return self.graph_db.are_connected(source, target)
    
    def run_db_ppr(self, reset_nodes: List[str], reset_weights: List[float] = None, 
                  damping: float = 0.85, directed: bool = None, max_iterations: int = 20) -> Dict[str, float]:
        """
        在数据库中直接执行个性化PageRank算法。
        这是一个底层方法，专注于数据库操作，不处理igraph兼容性。
        
        参数:
            reset_nodes (List[str]): 重置概率分布节点名称列表
            reset_weights (List[float], optional): 对应reset_nodes的权重列表，如果为None则均匀分布
            damping (float): 阻尼系数，默认0.85
            directed (bool, optional): 是否考虑图的有向性，如果为None则使用图的默认设置
            max_iterations (int): 最大迭代次数，默认20
            
        返回:
            Dict[str, float]: 节点名称到PageRank分数的映射
        """
        import time
        start_time = time.time()
        
        # 如果没有提供重置节点，返回空结果
        if not reset_nodes:
            return {}
        
        # 如果没有提供有向性参数，使用图的默认设置
        if directed is None:
            directed = self.directed
            
        # 如果reset_nodes太多，考虑只使用权重最高的一部分
        if len(reset_nodes) > 50:
            # 排序节点和权重
            node_weight_pairs = sorted(
                zip(reset_nodes, reset_weights or [1.0/len(reset_nodes)] * len(reset_nodes)),
                key=lambda x: x[1], reverse=True
            )
            # 只使用前50个最重要的节点
            reset_nodes = [n for n, _ in node_weight_pairs[:50]]
            reset_weights = [w for _, w in node_weight_pairs[:50]]
            logger.info(f"重置节点太多，截取前50个权重最高的节点，总权重: {sum(reset_weights):.4f}")
        
        # 创建缓存键 - 对于高频调用的场景非常重要
        cache_key = (
            tuple(sorted(zip(reset_nodes, reset_weights or [1.0/len(reset_nodes)] * len(reset_nodes)))),
            damping,
            directed,
            max_iterations
        )
        
        # 检查缓存
        if hasattr(self, '_ppr_cache') and cache_key in self._ppr_cache:
            logger.info("使用缓存的PPR结果")
            return self._ppr_cache[cache_key]
            
        # 调用数据库执行PageRank
        results = self.graph_db.run_ppr(reset_nodes, reset_weights, damping, directed, max_iterations)
        
        # 缓存结果 - 初始化缓存如果不存在
        if not hasattr(self, '_ppr_cache'):
            self._ppr_cache = {}
            self._ppr_cache_max_size = 50  # 限制缓存大小
            
        # 管理缓存大小
        if len(self._ppr_cache) >= self._ppr_cache_max_size:
            # 移除最旧的项
            self._ppr_cache.pop(next(iter(self._ppr_cache)))
            
        # 添加到缓存
        self._ppr_cache[cache_key] = results
        
        end_time = time.time()
        logger.info(f"数据库PPR计算完成，获取了 {len(results)} 个结果，耗时: {end_time - start_time:.2f}秒")
        return results
        
    def delete_edges(self, edges) -> None:
        """
        删除边
        
        参数:
            edges: 可以是单个边索引、边索引列表，或者边对象
        """
        # 将输入统一转换为列表形式
        if not isinstance(edges, list):
            edges = [edges]
            
        if not edges:
            return
            
        # 如果es属性存在，直接使用
        if hasattr(self, 'es'):
            self.es.delete(edges)
        else:
            logger.error("图中没有边序列信息，无法删除边")
            
    def add_edge(self, source, target, **kwargs):
        """
        添加单条边
        
        参数:
            source: 源节点索引或名称
            target: 目标节点索引或名称
            kwargs: 边的属性
        """
        # 转换源节点和目标节点为名称
        if isinstance(source, int):
            if hasattr(self, 'vs') and 0 <= source < len(self.vs):
                source_name = self.vs[source]['name']
            else:
                logger.error(f"源节点索引 {source} 无效")
                return
        else:
            source_name = source
            
        if isinstance(target, int):
            if hasattr(self, 'vs') and 0 <= target < len(self.vs):
                target_name = self.vs[target]['name']
            else:
                logger.error(f"目标节点索引 {target} 无效")
                return
        else:
            target_name = target
            
        # 构建边属性
        edge = {
            'source': source_name,
            'target': target_name
        }
        edge.update(kwargs)
        
        # 添加边
        self.graph_db.add_edges([edge])
        
    def merge_duplicate_edges(self):
        """
        合并数据库中的重复边
        
        查找并合并具有相同源和目标节点的边，合并策略为累加权重。
        此方法专门用于直接数据库模式，减少边的数量，提高PageRank计算效率。
        
        返回:
            Tuple[int, int]: (合并前边数, 合并后边数)
        """
        if not self.graph_db.connected:
            if not self.graph_db.connect():
                logger.error("无法连接到图数据库，合并操作失败")
                return (0, 0)
                
        logger.info("开始在数据库中合并重复边...")
        
        # 记录原始边数
        original_edge_count = self.ecount()
        logger.info(f"合并前边数量: {original_edge_count}")
        
        try:
            with self.graph_db.driver.session() as session:
                # 首先获取所有边的基本信息
                query = """
                MATCH (src:Node)-[r:RELATES_TO]->(tgt:Node)
                RETURN src.name AS source, tgt.name AS target, 
                       COLLECT(id(r)) AS edge_ids, SUM(r.weight) AS total_weight
                """
                result = session.run(query)
                
                # 处理每对节点间的边
                merged_count = 0
                deleted_count = 0
                
                for record in result:
                    source_name = record["source"]
                    target_name = record["target"]
                    edge_ids = record["edge_ids"]
                    total_weight = record["total_weight"]
                    
                    # 如果有多条边连接相同的节点对
                    if len(edge_ids) > 1:
                        merged_count += 1
                        deleted_count += len(edge_ids) - 1
                        
                        # 保留第一条边，更新其权重
                        keep_edge_id = edge_ids[0]
                        delete_edge_ids = edge_ids[1:]
                        
                        # 更新保留的边权重
                        session.run("""
                        MATCH ()-[r:RELATES_TO]->()
                        WHERE id(r) = $edge_id
                        SET r.weight = $weight
                        """, edge_id=keep_edge_id, weight=total_weight)
                        
                        # 删除重复的边
                        for edge_id in delete_edge_ids:
                            session.run("""
                            MATCH ()-[r:RELATES_TO]->()
                            WHERE id(r) = $edge_id
                            DELETE r
                            """, edge_id=edge_id)
                
                # 记录合并后边数
                new_edge_count = self.ecount()
                logger.info(f"合并完成，合并了 {merged_count} 组边，删除了 {deleted_count} 条重复边")
                logger.info(f"合并后边数量: {new_edge_count}")
                
                return (original_edge_count, new_edge_count)
                
        except Exception as e:
            logger.error(f"合并重复边时发生错误: {str(e)}")
            return (original_edge_count, original_edge_count)  # 返回原始边数表示没有变化
            
    def optimize_graph(self):
        """
        优化数据库图结构
        
        执行一系列优化操作：
        1. 合并重复边
        2. 分析图结构
        
        返回:
            Dict: 包含优化结果的统计信息
        """
        logger.info("开始优化数据库图结构...")
        
        stats = {}
        
        # 记录优化前图的基本信息
        before_node_count = self.vcount()
        before_edge_count = self.ecount()
        
        # 合并重复边
        before_count, after_count = self.merge_duplicate_edges()
        stats["edge_reduction"] = before_count - after_count
        stats["edge_reduction_percent"] = round((before_count - after_count) / before_count * 100, 2) if before_count > 0 else 0
        
        # 记录优化后图的基本信息
        after_node_count = self.vcount()
        after_edge_count = self.ecount()
        
        stats["before_node_count"] = before_node_count  
        stats["before_edge_count"] = before_edge_count
        stats["after_node_count"] = after_node_count
        stats["after_edge_count"] = after_edge_count
        
        logger.info(f"图优化完成: 节点数 {after_node_count}, 边数 {after_edge_count}")
        logger.info(f"减少了 {stats['edge_reduction']} 条边 ({stats['edge_reduction_percent']}%)")
        
        return stats
        
    def personalized_pagerank(self, vertices=None, damping=0.85, directed=None, weights=None, reset=None, implementation="prpack"):
        """
        模拟igraph的personalized_pagerank方法，提供与igraph完全兼容的接口。
        
        参数:
            vertices: 要计算PageRank的节点索引列表。默认为所有节点。
            damping: 阻尼系数，默认0.85
            directed: 是否考虑图的有向性，如果为None则使用图的默认设置
            weights: 边权重。可以是'weight'字符串表示使用边的weight属性，或权重列表。
            reset: 重置概率分布向量
            implementation: 算法实现，在数据库模式下此参数被忽略
            
        返回:
            np.ndarray: 各节点的PageRank分数数组
        """
        import numpy as np
        import time
        start_time = time.time()
        
        # 确保vertices默认为所有节点
        if vertices is None:
            vertices = range(self.vcount())
            
        # 确保reset默认为均匀分布
        if reset is None:
            reset = np.ones(self.vcount()) / self.vcount()
            
        # 优化1: 过滤掉权重为0的节点，减少计算量
        node_names = self.vs['name']
        reset_nodes = []
        reset_weights = []
        
        # 只处理vertices内的节点，并且只保留权重大于阈值的节点
        zero_threshold = 1e-6  # 权重阈值，低于此值视为0
        total_weight = 0.0
        
        for idx in vertices:
            if idx < len(node_names):
                node_name = node_names[idx]
                weight = reset[idx] if idx < len(reset) else 0.0
                if weight > zero_threshold:
                    reset_nodes.append(node_name)
                    reset_weights.append(weight)
                    total_weight += weight
        
        # 如果没有有效节点，返回零向量
        if not reset_nodes:
            logger.info("没有有效的重置节点，返回零向量")
            return np.zeros(self.vcount())
            
        # 归一化权重
        if total_weight > 0:
            reset_weights = [w / total_weight for w in reset_weights]
        
        # 优化2: 减少最大迭代次数，对于大多数应用场景，10-15次迭代通常足够
        max_iterations = 15
        if hasattr(self, 'global_config') and hasattr(self.global_config, 'num_ppr_iter'):
            max_iterations = min(self.global_config.num_ppr_iter, 20)  # 限制最大迭代次数
        
        # 调用基础方法
        logger.info(f"执行PPR计算: {len(reset_nodes)}个源节点，阻尼系数={damping}")
        try:
            pagerank_dict = self.run_db_ppr(
                reset_nodes=reset_nodes,
                reset_weights=reset_weights,
                damping=damping,
                directed=directed,
                max_iterations=max_iterations
            )
            
            # 优化3: 使用numpy向量化操作处理结果
            result_array = np.zeros(self.vcount())
            node_name_to_idx = {name: idx for idx, name in enumerate(node_names)}
            
            # 批量处理，避免在循环中多次查找
            for node_name, score in pagerank_dict.items():
                if node_name in node_name_to_idx:
                    result_array[node_name_to_idx[node_name]] = score
                    
            end_time = time.time()
            logger.info(f"PPR计算完成，耗时: {end_time - start_time:.2f}秒")
            return result_array
            
        except Exception as e:
            logger.error(f"执行personalized_pagerank时出错: {str(e)}，返回零向量")
            end_time = time.time()
            logger.error(f"PPR计算失败，耗时: {end_time - start_time:.2f}秒")
            return np.zeros(self.vcount())
    
    # 模拟igraph的vs和es属性
    @property
    def vs(self):
        """模拟igraph的顶点序列接口"""
        return DirectDBVertexSeq(self.graph_db)
    
    @property
    def es(self):
        """模拟igraph的边序列接口"""
        return DirectDBEdgeSeq(self.graph_db)


class DirectDBVertexSeq:
    """模拟igraph的VertexSeq类"""
    
    def __init__(self, graph_db: GraphDBConnector):
        self.graph_db = graph_db
        
        # 在初始化时获取所有节点名称，避免频繁查询数据库
        self._fetch_all_node_names()
    
    def _fetch_all_node_names(self):
        """获取所有节点名称"""
        try:
            with self.graph_db.driver.session() as session:
                result = session.run("MATCH (n:Node) RETURN n.name AS name")
                self._all_names = [record["name"] for record in result]
                
        except Exception as e:
            logger.error(f"获取所有节点名称失败: {str(e)}")
            self._all_names = []
    
    def attribute_names(self) -> List[str]:
        """
        获取所有节点属性名称
        
        返回:
            List[str]: 属性名称列表
        """
        try:
            with self.graph_db.driver.session() as session:
                # 获取第一个节点的所有属性
                if len(self._all_names) > 0:
                    first_node = self._all_names[0]
                    result = session.run("MATCH (n:Node {name: $name}) RETURN keys(n) AS attrs", name=first_node)
                    record = result.single()
                    if record and "attrs" in record:
                        return record["attrs"]
                
                # 如果没有节点或获取失败，尝试获取所有节点的所有属性
                result = session.run("MATCH (n:Node) UNWIND keys(n) AS key RETURN DISTINCT key")
                return [record["key"] for record in result]
                
        except Exception as e:
            logger.error(f"获取节点属性名称失败: {str(e)}")
            return ["name"]  # 至少返回name属性
    
    def __iter__(self):
        """
        迭代所有节点，返回DirectDBVertex对象
        """
        for idx, name in enumerate(self._all_names):
            yield DirectDBVertex(name, idx, self.graph_db)
    
    def __getitem__(self, key: str) -> List:
        """
        获取节点属性
        
        参数:
            key (str): 属性名称
            
        返回:
            List: 属性值列表
        """
        if key == 'name':
            return self._all_names
            
        try:
            with self.graph_db.driver.session() as session:
                result = session.run(f"MATCH (n:Node) RETURN n.{key} AS value")
                return [record["value"] for record in result]
                
        except Exception as e:
            logger.error(f"获取节点属性 {key} 失败: {str(e)}")
            return []
    
    def __len__(self) -> int:
        """返回节点数量"""
        return len(self._all_names)
    
    def find(self, name: str = None, **kwargs):
        """
        查找节点
        
        参数:
            name (str): 节点名称
            
        返回:
            DirectDBVertex: 表示找到的节点
        """
        if name is not None:
            try:
                idx = self._all_names.index(name)
                return DirectDBVertex(name, idx, self.graph_db)
            except ValueError:
                raise ValueError(f"找不到名称为 {name} 的节点")
        
        # 根据其他属性查找节点
        attrs = []
        params = {}
        for key, value in kwargs.items():
            attrs.append(f"n.{key} = ${key}")
            params[key] = value
            
        if not attrs:
            raise ValueError("必须提供至少一个查找条件")
            
        attr_str = " AND ".join(attrs)
        
        try:
            with self.graph_db.driver.session() as session:
                result = session.run(f"MATCH (n:Node) WHERE {attr_str} RETURN n.name AS name LIMIT 1", **params)
                record = result.single()
                
                if not record:
                    raise ValueError("找不到匹配的节点")
                    
                name = record["name"]
                idx = self._all_names.index(name)
                return DirectDBVertex(name, idx, self.graph_db)
                
        except Exception as e:
            logger.error(f"查找节点失败: {str(e)}")
            raise ValueError("查找节点时出错")


class DirectDBVertex:
    """表示直接数据库图中的节点"""
    
    def __init__(self, name: str, index: int, graph_db: GraphDBConnector):
        self.name = name
        self.index = index
        self.graph_db = graph_db
        
    def __getitem__(self, key: str):
        """获取节点属性"""
        if key == 'name':
            return self.name
            
        try:
            with self.graph_db.driver.session() as session:
                result = session.run(f"MATCH (n:Node {{name: $name}}) RETURN n.{key} AS value", name=self.name)
                record = result.single()
                return record["value"] if record else None
                
        except Exception as e:
            logger.error(f"获取节点属性 {key} 失败: {str(e)}")
            return None
    
    def attributes(self):
        """
        获取节点的所有属性
        
        返回:
            Dict: 包含所有属性的字典
        """
        try:
            with self.graph_db.driver.session() as session:
                result = session.run("MATCH (n:Node {name: $name}) RETURN properties(n) AS props", name=self.name)
                record = result.single()
                if record and "props" in record:
                    return record["props"]
                return {"name": self.name}
        except Exception as e:
            logger.error(f"获取节点属性失败: {str(e)}")
            return {"name": self.name}


class DirectDBEdgeSeq:
    """模拟igraph的EdgeSeq类"""
    
    def __init__(self, graph_db: GraphDBConnector):
        self.graph_db = graph_db
        # 缓存边信息以提高性能
        self._fetch_all_edges()
        
    def _fetch_all_edges(self):
        """获取所有边的信息"""
        try:
            self._edges = []
            with self.graph_db.driver.session() as session:
                # 先获取所有节点名称，确保建立正确的索引映射
                nodes_result = session.run("MATCH (n:Node) RETURN n.name AS name")
                node_names = [record["name"] for record in nodes_result]
                self._node_name_to_idx = {name: idx for idx, name in enumerate(node_names)}
                
                # 获取所有边的基本信息
                result = session.run("""
                    MATCH (src:Node)-[r:RELATES_TO]->(tgt:Node)
                    RETURN id(r) AS id, src.name AS source_name, tgt.name AS target_name,
                           properties(r) AS properties
                """)
                
                # 处理边结果
                for record in result:
                    edge_id = record["id"]
                    source_name = record["source_name"]
                    target_name = record["target_name"]
                    properties = record["properties"]
                    
                    # 获取源节点和目标节点的索引
                    source_idx = self._node_name_to_idx.get(source_name, -1)
                    target_idx = self._node_name_to_idx.get(target_name, -1)
                    
                    if source_idx >= 0 and target_idx >= 0:
                        # 创建边对象
                        edge = DirectDBEdge(
                            edge_id=edge_id,
                            source=source_idx,  # 一定使用整数索引
                            target=target_idx,  # 一定使用整数索引
                            source_name=source_name,
                            target_name=target_name,
                            properties=properties
                        )
                        self._edges.append(edge)
                
                logger.debug(f"已获取 {len(self._edges)} 条边")
                
        except Exception as e:
            logger.error(f"获取边信息失败: {str(e)}")
            self._edges = []
            self._node_name_to_idx = {}
            
    def __iter__(self):
        """
        迭代所有边，返回DirectDBEdge对象
        """
        return iter(self._edges)
    
    def __getitem__(self, key_or_idx):
        """
        获取边属性或特定边
        
        参数:
            key_or_idx: 属性名称(str)或边索引(int)
            
        返回:
            如果key_or_idx是字符串: 属性值列表
            如果key_or_idx是整数: 特定边对象
        """
        if isinstance(key_or_idx, int):
            # 返回特定索引的边
            if 0 <= key_or_idx < len(self._edges):
                return self._edges[key_or_idx]
            raise IndexError(f"边索引 {key_or_idx} 超出范围")
            
        # 否则视为属性名称
        key = key_or_idx
        try:
            # 从缓存的边中获取属性
            if key == 'source':
                return [edge.source for edge in self._edges]
            elif key == 'target':
                return [edge.target for edge in self._edges]
            else:
                return [edge.properties.get(key) for edge in self._edges]
                
        except Exception as e:
            logger.error(f"获取边属性 {key} 失败: {str(e)}")
            return []
    
    def __len__(self) -> int:
        """返回边数量"""
        return len(self._edges)
            
    def attribute_names(self):
        """返回所有边的属性名称"""
        if not self._edges:
            return ["weight"]
            
        # 收集所有属性名称
        names = set()
        for edge in self._edges:
            names.update(edge.properties.keys())
            
        # 添加标准属性
        names.update(["source", "target", "weight"])
        return list(names)
        
    def select(self, **kwargs):
        """
        根据属性选择边
        
        参数:
            **kwargs: 属性名和值的键值对
            
        返回:
            List[int]: 匹配边的索引列表
        """
        result = []
        for idx, edge in enumerate(self._edges):
            match = True
            for key, value in kwargs.items():
                if key == "source":
                    if edge.source != value:
                        match = False
                        break
                elif key == "target":
                    if edge.target != value:
                        match = False
                        break
                elif edge.properties.get(key) != value:
                    match = False
                    break
                    
            if match:
                result.append(idx)
                
        return result
        
    def find(self, **kwargs):
        """
        查找满足条件的边
        
        参数:
            **kwargs: 属性名和值的键值对
            
        返回:
            DirectDBEdge: 匹配的第一条边
        """
        indices = self.select(**kwargs)
        if indices:
            return self._edges[indices[0]]
        raise ValueError("找不到匹配的边")
    
    def delete(self, edge_indices):
        """
        删除指定的边
        
        参数:
            edge_indices: 单个边索引或边索引列表
        """
        if not isinstance(edge_indices, list):
            edge_indices = [edge_indices]
            
        if not edge_indices:
            return
            
        try:
            with self.graph_db.driver.session() as session:
                for idx in sorted(edge_indices, reverse=True):
                    if 0 <= idx < len(self._edges):
                        edge = self._edges[idx]
                        # 从数据库中删除边
                        session.run("""
                            MATCH (src:Node {name: $source_name})-[r:RELATES_TO]->(tgt:Node {name: $target_name})
                            DELETE r
                        """, source_name=edge.source_name, target_name=edge.target_name)
                        
                # 重新获取边信息
                self._fetch_all_edges()
                logger.info(f"已删除 {len(edge_indices)} 条边")
                
        except Exception as e:
            logger.error(f"删除边失败: {str(e)}")


class DirectDBEdge:
    """表示数据库图中的边"""
    
    def __init__(self, edge_id, source, target, source_name, target_name, properties=None):
        """
        初始化边对象
        
        参数:
            edge_id: 边的唯一ID
            source: 源节点索引
            target: 目标节点索引
            source_name: 源节点名称
            target_name: 目标节点名称
            properties: 边的属性字典
        """
        self.id = edge_id
        self._source = source  # 源节点索引
        self._target = target  # 目标节点索引
        self.source_name = source_name
        self.target_name = target_name
        self.properties = properties or {}
        
    @property
    def source(self):
        """获取源节点索引，确保与igraph兼容"""
        return self._source
        
    @property
    def target(self):
        """获取目标节点索引，确保与igraph兼容"""
        return self._target
        
    def __getitem__(self, key):
        """获取边的属性"""
        if key == 'source':
            return self._source
        elif key == 'target':
            return self._target
        elif key == 'name':
            return f"{self.source_name}->{self.target_name}"
        return self.properties.get(key)
        
    def __setitem__(self, key, value):
        """设置边的属性"""
        if key == 'source':
            self._source = value
        elif key == 'target':
            self._target = value
        else:
            self.properties[key] = value
            
    def attributes(self):
        """返回边的所有属性"""
        attrs = self.properties.copy()
        attrs['source'] = self._source
        attrs['target'] = self._target
        return attrs
    
    def attribute_names(self):
        """返回边的所有属性名称"""
        names = list(self.properties.keys())
        names.extend(['source', 'target', 'weight'])
        return names 