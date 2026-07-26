import os
from dataclasses import dataclass, field
from typing import (
    Literal,
    Union,
    Optional
)

from .logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class BaseConfig:
    """基础配置类，用于管理所有系统配置项。"""
    # API配置部分
    llm_base_url: Optional[str] = field(default=None)  # LLM API的基础URL
    llm_name: str = field(default="gpt-3.5-turbo")  # 使用的LLM模型名称
    llm_api_key: Optional[str] = field(default=None)  # LLM API密钥
    embedding_base_url: Optional[str] = field(default=None)  # 嵌入API的基础URL
    embedding_api_key: Optional[str] = field(default=None)  # 嵌入API密钥
    embedding_model_name: str = field(default="text-embedding-3-small")  # 嵌入模型名称

    # 系统基础配置
    save_dir: str = field(default="outputs")  # 输出保存目录
    embedding_batch_size: int = field(default=16)  # 嵌入批处理大小
    embedding_max_seq_len: int = field(default=512)  # 嵌入最大序列长度
    embedding_return_as_normalized: bool = field(default=True)  # 是否返回归一化的嵌入向量
    max_new_tokens: int = field(default=4096)  # 生成的最大新token数
    is_directed_graph: bool = field(default=True)  # 是否使用有向图
    force_index_from_scratch: bool = field(default=False)  # 是否强制从头开始建立索引
    save_openie: bool = field(default=True)  # 是否保存开放信息抽取结果

    # 图数据库配置
    use_graph_db: bool = field(
        default=False,
        metadata={"help": "是否使用图数据库而非文件存储图数据"}
    )
    graph_db_type: Literal["neo4j", "tigergraph", "neptune", "arangodb", "orientdb"] = field(
        default="neo4j",
        metadata={"help": "使用的图数据库类型"}
    )
    graph_db_url: Optional[str] = field(
        default=None,
        metadata={"help": "图数据库连接URL"}
    )
    graph_db_name: Optional[str] = field(
        default="hipporag",
        metadata={"help": "图数据库名称"}
    )
    graph_db_username: Optional[str] = field(
        default=None, 
        metadata={"help": "图数据库用户名"}
    )
    graph_db_password: Optional[str] = field(
        default=None,
        metadata={"help": "图数据库密码"}
    )
    graph_db_port: Optional[int] = field(
        default=None,
        metadata={"help": "图数据库端口"}
    )

    # LLM特定属性
    embedding_api_base: str = field(
        default=None,
        metadata={"help": "embedding_base_url的别名，用于兼容性"}
    )
    max_retry_attempts: int = field(
        default=5,
        metadata={"help": "异步API调用的最大重试次数"}
    )
    
    # 存储相关属性
    force_openie_from_scratch: bool = field(
        default=False,
        metadata={"help": "如果设为True，将忽略所有现有的openie文件并从头重建"}
    )
    rerank_dspy_file_path: str = field(
        default=None,
        metadata={"help": "重排序dspy文件的路径"}
    )
    passage_node_weight: float = field(
        default=0.05,
        metadata={"help": "PPR中段落节点权重的乘数因子"}
    )
    
    # 预处理相关属性
    text_preprocessor_class_name: str = field(
        default="TextPreprocessor",
        metadata={"help": "用于预处理的文本预处理器类名"}
    )
    preprocess_encoder_name: str = field(
        default="gpt-4o",
        metadata={"help": "预处理中使用的编码器名称（当前特别用于文档分块）"}
    )
    preprocess_chunk_overlap_token_size: int = field(
        default=128,
        metadata={"help": "相邻块之间的重叠token数"}
    )
    preprocess_chunk_max_token_size: int = field(
        default=None,
        metadata={"help": "每个块可以包含的最大token数。如果设为None，整个文档将被视为单个块"}
    )
    preprocess_chunk_func: Literal["by_token", "by_word"] = field(default='by_token')  # 分块方式
    
    # 信息抽取相关属性
    information_extraction_model_name: Literal["openie_openai_gpt", ] = field(
        default="openie_openai_gpt",
        metadata={"help": "指示使用哪个信息抽取模型的类名"}
    )
    openie_mode: Literal["offline", "online"] = field(
        default="online",
        metadata={"help": "OpenIE模型的运行模式"}
    )
    skip_graph: bool = field(
        default=False,
        metadata={"help": "是否跳过图构建。首次运行vllm离线索引时设为true"}
    )
    
    # 嵌入相关属性
    embedding_model_dtype: Literal["float16", "float32", "bfloat16", "auto"] = field(
        default="auto",
        metadata={"help": "本地嵌入模型的数据类型"}
    )
    
    # 图构建相关属性
    synonymy_edge_topk: int = field(
        default=2047,
        metadata={"help": "构建同义边时knn检索的k值"}
    )
    synonymy_edge_query_batch_size: int = field(
        default=1000,
        metadata={"help": "构建同义边时查询嵌入的批处理大小"}
    )
    synonymy_edge_key_batch_size: int = field(
        default=10000,
        metadata={"help": "构建同义边时键嵌入的批处理大小"}
    )
    synonymy_edge_sim_threshold: float = field(
        default=0.8,
        metadata={"help": "包含候选同义节点的相似度阈值"}
    )
    
    # 检索相关属性
    linking_top_k: int = field(
        default=5,
        metadata={"help": "每个检索步骤中链接节点的数量"}
    )
    retrieval_top_k: int = field(
        default=200,
        metadata={"help": "每步检索的文档数量"}
    )
    damping: float = field(
        default=0.5,
        metadata={"help": "PPR算法的阻尼因子"}
    )
    
    # 问答相关属性
    max_qa_steps: int = field(
        default=1,
        metadata={"help": "回答单个问题时，用于交替检索和推理的最大步骤数"}
    )
    qa_top_k: int = field(
        default=5,
        metadata={"help": "提供给QA模型阅读的top k文档数"}
    )
    
    # 数据集运行相关属性
    ## 通用属性
    dataset: Optional[Literal['hotpotqa', 'hotpotqa_train', 'musique', '2wikimultihopqa']] = field(
        default=None,
        metadata={"help": "使用的数据集。如果指定，表示我们将运行特定数据集；如果未指定，表示自由运行"}
    )
    ## 图相关属性
    graph_type: Literal[
        'dpr_only', 
        'entity', 
        'passage_entity', 'relation_aware_passage_entity',
        'passage_entity_relation', 
        'facts_and_sim_passage_node_unidirectional',
    ] = field(
        default="facts_and_sim_passage_node_unidirectional",
        metadata={"help": "实验中使用的图类型"}
    )
    corpus_len: Optional[int] = field(
        default=None,
        metadata={"help": "使用的语料库长度"}
    )
    
    def __post_init__(self):
        """初始化后的处理，设置默认值和环境变量"""
        # 设置默认值
        if self.llm_base_url is None:
            self.llm_base_url = os.getenv("LLM_BASE_URL")
        if self.llm_api_key is None:
            self.llm_api_key = os.getenv("LLM_API_KEY")
        if self.embedding_base_url is None:
            self.embedding_base_url = os.getenv("EMBEDDING_API_BASE")
        if self.embedding_api_key is None:
            self.embedding_api_key = os.getenv("EMBEDDING_API_KEY")
        if self.save_dir is None: # 如果未指定保存目录
            if self.dataset is None: self.save_dir = 'outputs' # 自由运行模式
            else: self.save_dir = os.path.join('outputs', self.dataset) # 根据数据集自定义输出目录
            
        # 图数据库环境变量
        if self.graph_db_url is None:
            self.graph_db_url = os.getenv("GRAPH_DB_URL")
        if self.graph_db_username is None:
            self.graph_db_username = os.getenv("GRAPH_DB_USERNAME")
        if self.graph_db_password is None:
            self.graph_db_password = os.getenv("GRAPH_DB_PASSWORD")
        if self.graph_db_port is None:
            port_env = os.getenv("GRAPH_DB_PORT")
            if port_env is not None:
                try:
                    self.graph_db_port = int(port_env)
                except ValueError:
                    logger.warning(f"无法将GRAPH_DB_PORT环境变量 '{port_env}' 转换为整数")
                    
        logger.debug(f"初始化最高级别的保存目录为 {self.save_dir}")
