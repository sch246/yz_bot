import os
import sys


from dotenv import load_dotenv
from hipporag import HippoRAG
from hipporag.utils.config_utils import BaseConfig

if __name__ == "__main__":
    # 加载环境变量
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
        openie_mode="online"
    )

    # 初始化HippoRAG
    hipporag = HippoRAG(global_config=config)

    # 准备测试文档
    docs = [
        "HippoRAG是一个基于图的检索增强生成框架。",
        "它使用知识图谱来连接文档中的实体和关系。",
        "这种方法可以提高多跳问题的检索效果。",
        "张三是一名程序员，他擅长Python编程。",
        "李四和张三是同事，他们在同一家科技公司工作。",
        "这家公司主要研发人工智能产品。"
    ]

    print("开始索引文档...")
    hipporag.index(docs=docs)
    print("文档索引完成！")

    # 准备测试问题
    queries = [
        "HippoRAG有什么特点？",
        "张三是做什么工作的？他和李四是什么关系？"
    ]

    print("\n开始执行检索...")
    retrieval_results = hipporag.retrieve(queries=queries, num_to_retrieve=3)
    print("检索完成！")

    print("\n开始执行问答...")
    qa_results = hipporag.rag_qa(retrieval_results)
    print("问答完成！")

    print("\n开始执行端到端RAG...")
    rag_results = hipporag.rag_qa(queries=queries)
    print("端到端RAG完成！")

    # 输出结果
    print("\n测试结果：")
    for i, query in enumerate(queries):
        print(f"\n问题: {query}")
        print(f"回答: {rag_results[1][i]}")
        print("---")
