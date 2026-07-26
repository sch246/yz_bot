import os
from dotenv import load_dotenv
from hipporag import HippoRAG
from hipporag.utils.config_utils import BaseConfig

load_dotenv()

# 准备配置
config = BaseConfig(
    llm_base_url=os.getenv("LLM_BASE_URL"),
    llm_name=os.getenv("LLM_NAME"),
    llm_api_key=os.getenv("LLM_API_KEY"),
    embedding_base_url=os.getenv("EMBEDDING_API_BASE"),
    embedding_api_key=os.getenv("EMBEDDING_API_KEY"),
    embedding_model_name=os.getenv("EMBEDDING_MODEL_NAME"),
    embedding_batch_size=16,
    graph_type="facts_and_sim_passage_node_unidirectional",
    max_new_tokens=4096,
    openie_mode="online",
    # 数据库
    use_graph_db=True,
    graph_db_type="neo4j",
    graph_db_url=os.getenv("NEO4J_URL"),
    graph_db_username=os.getenv("NEO4J_USERNAME"),
    graph_db_password=os.getenv("NEO4J_PASSWORD"),
)

# 初始化HippoRAG
hipporag = HippoRAG(global_config=config, direct_db_mode=True)
import atexit
atexit.register(hipporag.force_save_graph)
