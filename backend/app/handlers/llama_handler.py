import json
import logging
import os
import re
import uuid

from llama_index import VectorStoreIndex, load_index_from_storage, StorageContext, ServiceContext, ComposableGraph, \
    ListIndex, Prompt, LLMPredictor, Document, TreeIndex
from llama_index.chat_engine import CondenseQuestionChatEngine
from llama_index.chat_engine.types import BaseChatEngine
from llama_index.indices.base import BaseIndex
from llama_index.indices.query.base import BaseQueryEngine

from configs.config import Prompts
from configs.embed_model import EmbedModelOption
from configs.llm_predictor import LLMPredictorOption
from configs.load_env import index_save_directory, FILE_PATH
from utils.file import get_folders_list
from utils.llama import get_nodes_from_file
from utils.logger import customer_logger

indexes = []


def createIndex(index_name):
    """
    创建索引
    :param index_name: 索引名称
    :return:
    """
    index = VectorStoreIndex([])
    index.set_index_id(index_name)
    logging.info(f"index保存位置: {index_save_directory + index_name}")
    index.storage_context.persist(os.path.join(index_save_directory, index_name))


def loadAllIndexes():
    """
    加载索引数据
    :param index_save_directory: 索引保存目录
    :return:
    """
    for index_dir_name in get_folders_list(index_save_directory):
        # 获取索引目录的完整路径
        index_dir_path = os.path.join(index_save_directory, index_dir_name)
        storage_context = StorageContext.from_defaults(persist_dir=index_dir_path)
        index = load_index_from_storage(storage_context)
        indexes.append(index)


def insert_into_index(index, doc_file_path, llm_predictor=None, embed_model=None):
    """
    通过文档路径插入index
    :param index: 索引
    :param doc_file_path: 文档路径
    :param input_files 文档列表
    :param llm_predictor: 语言模型预测器
    :param embed_model: 嵌入模型
    :return:
    """
    # 使用自定义的 llm_predictor 或默认值
    if llm_predictor is None:
        llm_predictor = LLMPredictorOption.GPT3_5.value
    # 使用自定义的 embed_model 或默认值
    if embed_model is None:
        embed_model = EmbedModelOption.DEFAULT.value
    service_context = ServiceContext.from_defaults(llm_predictor=llm_predictor, embed_model=embed_model)
    nodes = get_nodes_from_file(doc_file_path)
    index.insert_nodes(nodes, context=service_context)

    # 生成summary maxRecursion
    index.summary = summary_index(index)
    index.storage_context.persist(persist_dir=os.path.join(index_save_directory, index.index_id))


def embeddingQA(index: BaseIndex, qa_pairs, id=str(uuid.uuid4())):
    """
    将拆分后的问答对插入索引
    :param index: 索引
    :param qa_pairs: 问答对
    :param id: 文档id
    :return:
    """
    # 使用自定义的 llm_predictor 或默认值
    llm_predictor = LLMPredictorOption.GPT3_5.value
    # 使用自定义的 embed_model 或默认值
    embed_model = EmbedModelOption.DEFAULT.value
    service_context = ServiceContext.from_defaults(llm_predictor=llm_predictor, embed_model=embed_model)
    for i in range(0, len(qa_pairs), 2):
        q = qa_pairs[i]
        if i + 1 < len(qa_pairs):
            a = qa_pairs[i + 1]
            doc = Document(text=f"{q} {a}", id_=id)
            customer_logger.info(f"{doc.text}")
            index.insert(doc, service_context=service_context)
        else:
            customer_logger.info(f"Last element': {qa_pairs[i]}")
    # 生成summary 会出问题
    # index.summary = summary_index(index)
    index.storage_context.persist(persist_dir=os.path.join(index_save_directory, index.index_id))


def get_all_docs(index_):
    """
    通过index，获取所有文档
    :param index_:
    :return:
    """
    all_docs = index_.docstore.docs.values()
    # 创建一个空列表，用于存储节点 ID 和内容
    documents = []
    for doc in all_docs:
        node_id = doc.node_id
        doc_id = doc.ref_doc_id
        doc_text = doc.get_content()
        documents.append({"doc_id": doc_id, "node_id": node_id, "text": doc_text})
    return documents


def updateNodeById(index_, id_, text):
    """
    :param index_: 索引
    :param id_: node_id
    :param text: 更改后的内容
    :return:
    """
    # node = index.docstore.get_node(id_)
    node = index_.docstore.docs[id_]
    node.set_content(text)
    index_.docstore.add_documents([node])


def deleteNodeById(index_, id_, ):
    """
    :param index_: 索引
    :param id_: node_id
    :return:
    """
    # TODO 记录删除的节点
    # content = index.docstore.get_node(id_).get_content()
    index_.docstore.delete_document(id_)


def deleteDocById(index, id):
    """
    # 删除文档
    :param id: 文档的id
    :return:
    """
    index.delete_ref_doc(id, delete_from_docstore=True)
    saveIndex(index)


def saveIndex(index):
    index.storage_context.persist(os.path.join(index_save_directory + index.index_id))


def compose_graph_chat_egine() -> BaseChatEngine:
    """
    将index合成为graph
    :return: chat_engine
    """
    if indexes is None:
        loadAllIndexes()
    summaries = []
    for i in indexes:
        summaries.append(i.summary)
    graph = ComposableGraph.from_indices(
        ListIndex,
        indexes,
        index_summaries=summaries,
    )

    custom_query_engines = {
        index.index_id: index.as_query_engine(
            child_branch_factor=2
        )
        for index in indexes
    }

    chat_engine = CondenseQuestionChatEngine.from_defaults(
        query_engine=graph.as_query_engine(text_qa_template=Prompts.QA_PROMPT.value,
                                           refine_template=Prompts.REFINE_PROMPT.value,
                                           streaming=True,
                                           similarity_top_k=3,
                                           verbose=True,
                                           custom_query_engines=custom_query_engines),
        condense_question_prompt=Prompts.CONDENSE_QUESTION_PROMPT.value,
        verbose=True,
        chat_mode="condense_question",
    )

    return chat_engine


def compose_graph_query_egine() -> BaseQueryEngine:
    """
    将index合成为graph
    :return: query_engine
    """
    if indexes is None:
        loadAllIndexes()
    summaries = []
    for i in indexes:
        summaries.append(i.summary)
    graph = ComposableGraph.from_indices(
        TreeIndex,
        indexes,
        index_summaries=summaries,
    )

    custom_query_engines = {
        index.index_id: index.as_query_engine(
            child_branch_factor=3
        )
        for index in indexes
    }

    query_engine = graph.as_query_engine(text_qa_template=Prompts.QA_PROMPT.value,
                                         refine_template=Prompts.REFINE_PROMPT.value,
                                         streaming=True,
                                         similarity_top_k=3,
                                         verbose=True,
                                         custom_query_engines=custom_query_engines)
    return query_engine


def summary_index(index):
    """
         生成 summary
    """
    summary = index.as_query_engine(response_mode="tree_summarize").query(
        "总结，生成文章摘要，要覆盖所有要点，方便后续检索"
    )
    # 去掉换行符、制表符、多余的空格和其他非字母数字字符
    summary_str = re.sub(r"\s+", " ", str(summary))
    summary_str = re.sub(r"[^\w\s]", "", summary_str)
    logging.info(f"Summary: {summary_str}")
    return summary_str


def get_history_msg(chat_engine: BaseChatEngine):
    """
    获取对话记录
    :param chat_engine:
    :return:
    """
    return chat_engine.chat_history


def get_index_by_name(index_name):
    index: VectorStoreIndex = None
    for i in indexes:
        if i.index_id == index_name:
            index = i
            break
    return index


def get_prompt_by_name(prompt_type):
    """获取Prompt"""
    return Prompt(getattr(Prompts, prompt_type.value))


def convert_index_to_file(index_name, file_name):
    """通过索引名称将索引中的文本提取出来，存入一个txt文件中"""
    path = os.path.join(index_save_directory, index_name, 'docstore.json')
    out_path = os.path.join(FILE_PATH, file_name)
    if not os.path.exists(FILE_PATH):
        os.makedirs(FILE_PATH)
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    text_list = []
    for node_id, node_data in data['docstore/data'].items():
        node_text = node_data['__data__']['text']
        if node_text is not None:
            # 去除空格和换行符
            node_text = node_text.strip().replace('\n', '').replace('\r', '')
            text_list.append(node_text)

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(text_list))


def citf(index, name):
    """将index转换为file"""
    path = os.path.join(FILE_PATH, name)
    if not os.path.exists(FILE_PATH):
        os.makedirs(FILE_PATH)
    data = index.docstore.docs
    text_list = []
    for node_id, node_data in data.items():
        for key, value in node_data:
            if key == 'text':
                node_text = value
                # 去除空格和换行符
                node_text = node_text.strip().replace('\n', '').replace('\r', '')
                text_list.append(node_text)

    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(text_list))


def format_source_nodes_list(node_with_score_list):
    formatted_nodes = []
    for node_with_score in node_with_score_list:
        formatted_node = {
            'id': node_with_score.node.id_,
            'text': node_with_score.node.text
        }
        formatted_nodes.append(formatted_node)
    return formatted_nodes


if __name__ == "__main__":
    loadAllIndexes()
    index = get_index_by_name('qa')

    node = index.docstore.get_node('41d0dfca-e2d1-4845-b330-4c88d2acce27')
    print(node.get_content())