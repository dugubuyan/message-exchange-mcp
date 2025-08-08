#!/usr/bin/env python3
"""
基于Gradio的MCP服务：消息发布订阅系统
"""

import asyncio
import json
import logging
import time
import threading
import uuid
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
import gradio as gr
from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
import mcp.server.stdio

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gradio-message-service-mcp")

# 服务器配置
BASE_URL = "http://localhost:8000"
server = Server("gradio-message-service-mcp")

# 用户ID持久化配置
USER_DATA_DIR = Path.home() / ".kiro" / "mcp" / "gradio-message-service"
USER_ID_FILE = USER_DATA_DIR / "user_id.json"

class UserManager:
    """用户管理器，负责用户ID的持久化"""
    
    def __init__(self):
        self.user_id = None
        self.user_data = {}
        self._ensure_data_dir()
        self._load_user_data()
    
    def _ensure_data_dir(self):
        """确保数据目录存在"""
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    def _load_user_data(self):
        """加载用户数据"""
        try:
            if USER_ID_FILE.exists():
                with open(USER_ID_FILE, 'r', encoding='utf-8') as f:
                    self.user_data = json.load(f)
                    self.user_id = self.user_data.get('user_id')
                    logger.info(f"加载已存在的用户ID: {self.user_id}")
            
            if not self.user_id:
                self._generate_new_user_id()
        except Exception as e:
            logger.error(f"加载用户数据失败: {e}")
            self._generate_new_user_id()
    
    def _generate_new_user_id(self):
        """生成新的用户ID"""
        self.user_id = str(uuid.uuid4())
        self.user_data = {
            'user_id': self.user_id,
            'created_at': time.time(),
            'display_name': f"用户_{self.user_id[:8]}"
        }
        self._save_user_data()
        logger.info(f"生成新用户ID: {self.user_id}")
    
    def _save_user_data(self):
        """保存用户数据"""
        try:
            with open(USER_ID_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.user_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存用户数据失败: {e}")
    
    def get_user_id(self) -> str:
        """获取当前用户ID"""
        return self.user_id
    
    def get_display_name(self) -> str:
        """获取用户显示名称"""
        return self.user_data.get('display_name', f"用户_{self.user_id[:8]}")
    
    def update_display_name(self, name: str):
        """更新用户显示名称"""
        self.user_data['display_name'] = name
        self._save_user_data()

# 全局用户管理器
user_manager = UserManager()

class MessageServiceClient:
    """消息服务客户端"""
    
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url
        self._client = None
    
    @property
    def client(self):
        """获取HTTP客户端，如果不存在或已关闭则创建新的"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
            )
        return self._client
    
    async def close(self):
        """关闭HTTP客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """发起HTTP请求"""
        url = f"{self.base_url}{endpoint}"
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                # 确保使用新的客户端连接
                client = self.client
                response = await client.request(method, url, **kwargs)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, httpx.ConnectError, httpx.TimeoutException) as e:
                logger.error(f"HTTP请求失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    raise Exception(f"请求失败: {str(e)}")
                # 关闭当前客户端，下次会创建新的
                await self.close()
                await asyncio.sleep(1)  # 等待1秒后重试
            except Exception as e:
                logger.error(f"请求处理错误: {e}")
                raise Exception(f"处理错误: {str(e)}")
    
    def _make_sync_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """发起同步HTTP请求（用于Gradio界面）"""
        url = f"{self.base_url}{endpoint}"
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                # 使用同步客户端，每次请求都创建新连接
                with httpx.Client(timeout=30.0) as client:
                    response = client.request(method, url, **kwargs)
                    response.raise_for_status()
                    return response.json()
            except (httpx.HTTPError, httpx.ConnectError, httpx.TimeoutException) as e:
                logger.error(f"同步HTTP请求失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    raise Exception(f"请求失败: {str(e)}")
                time.sleep(1)  # 等待1秒后重试
            except Exception as e:
                logger.error(f"同步请求处理错误: {e}")
                raise Exception(f"处理错误: {str(e)}")

# 全局客户端实例
message_client = MessageServiceClient()

# Gradio界面函数（使用同步请求和自动用户ID）
def subscribe_topic_ui(topic: str):
    """订阅topic的UI函数"""
    try:
        user_id = user_manager.get_user_id()
        result = message_client._make_sync_request(
            "POST", "/topics/subscribe",
            json={"user_id": user_id, "topic": topic}
        )
        return f"🎉 成功加入{topic}话题! {json.dumps(result, ensure_ascii=False, indent=2)}"
    except Exception as e:
        return f"❌ 加入社区失败: {str(e)}"

def unsubscribe_topic_ui(topic: str):
    """取消订阅topic的UI函数"""
    try:
        user_id = user_manager.get_user_id()
        result = message_client._make_sync_request(
            "POST", "/topics/unsubscribe",
            json={"user_id": user_id, "topic": topic}
        )
        return f"👋 已离开{topic}话题 {json.dumps(result, ensure_ascii=False, indent=2)}"
    except Exception as e:
        return f"❌ 离开社区失败: {str(e)}"

def get_topics_ui():
    """获取所有topics的UI函数"""
    try:
        result = message_client._make_sync_request("GET", "/topics")
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"❌ 获取topics失败: {str(e)}"

def publish_request_ui(topic: str, title: str, content: str):
    """发布需求消息的UI函数"""
    try:
        user_id = user_manager.get_user_id()
        payload = {
            "user_id": user_id,
            "topic": topic,
            "content": content
        }
        if title.strip():
            payload["title"] = title
        
        result = message_client._make_sync_request(
            "POST", "/requests/publish", json=payload
        )
        return f"🚀 帖子发布成功! {json.dumps(result, ensure_ascii=False, indent=2)}"
    except Exception as e:
        return f"❌ 发布失败: {str(e)}"

def publish_response_ui(request_id: str, content: str):
    """发布应答消息的UI函数"""
    try:
        user_id = user_manager.get_user_id()
        result = message_client._make_sync_request(
            "POST", "/responses/publish",
            json={
                "user_id": user_id,
                "request_id": request_id,
                "content": content
            }
        )
        return f"💬 回复发布成功! {json.dumps(result, ensure_ascii=False, indent=2)}"
    except Exception as e:
        return f"❌ 应答发布失败: {str(e)}"

def get_my_requests_ui():
    """获取我发布的需求消息"""
    try:
        user_id = user_manager.get_user_id()
        requests = message_client._make_sync_request(
            "GET", f"/users/{user_id}/requests"
        )
        
        # 检查返回的数据类型
        if isinstance(requests, str):
            # 如果返回的是字符串，直接返回
            return f"📝 我的帖子:\n{requests}"
        
        if not requests or len(requests) == 0:
            return "📝 你还没有发布任何帖子"
        
        # 如果是列表，进行格式化
        if isinstance(requests, list):
            formatted_requests = []
            for req in requests:
                if isinstance(req, dict):
                    formatted_requests.append({
                        "帖子ID": req.get("id", "N/A"),
                        "标题": req.get("title", "无标题"),
                        "内容": req.get("content", ""),
                        "话题": req.get("topic", ""),
                        "发布时间": req.get("created_at", ""),
                        "状态": req.get("status", "")
                    })
                else:
                    # 如果列表中的元素不是字典，直接添加
                    formatted_requests.append(req)
            
            return json.dumps(formatted_requests, ensure_ascii=False, indent=2)
        
        # 其他情况，直接返回JSON格式
        return json.dumps(requests, ensure_ascii=False, indent=2)
        
    except Exception as e:
        return f"❌ 获取我的需求失败: {str(e)}"

def get_my_responses_ui():
    """获取我收到的应答消息"""
    try:
        user_id = user_manager.get_user_id()
        responses = message_client._make_sync_request(
            "GET", f"/users/{user_id}/received/responses"
        )
        
        # 检查返回的数据类型
        if isinstance(responses, str):
            # 如果返回的是字符串，直接返回
            return f"📬 我收到的回复:\n{responses}"
        
        if not responses or len(responses) == 0:
            return "📬 暂无收到的回复"
        
        # 如果是列表，进行格式化
        if isinstance(responses, list):
            formatted_responses = []
            for resp in responses:
                if isinstance(resp, dict):
                    formatted_responses.append({
                        "回复ID": resp.get("id", "N/A"),
                        "原帖ID": resp.get("request_id", "N/A"),
                        "回复内容": resp.get("content", ""),
                        "回复者": resp.get("user_id", ""),
                        "回复时间": resp.get("created_at", "")
                    })
                else:
                    # 如果列表中的元素不是字典，直接添加
                    formatted_responses.append(resp)
            
            return json.dumps(formatted_responses, ensure_ascii=False, indent=2)
        
        # 其他情况，直接返回JSON格式
        return json.dumps(responses, ensure_ascii=False, indent=2)
        
    except Exception as e:
        return f"❌ 获取我的应答失败: {str(e)}"

def get_subscribed_requests_ui():
    """获取我订阅的topic收到的需求消息"""
    try:
        user_id = user_manager.get_user_id()
        received_requests = message_client._make_sync_request(
            "GET", f"/users/{user_id}/received/requests"
        )
        
        # 检查返回的数据类型
        if isinstance(received_requests, str):
            # 如果返回的是字符串，直接返回
            return f"📥 你的个人Feed:\n{received_requests}"
        
        if not received_requests or len(received_requests) == 0:
            return "📥 你的Feed中暂无新帖子"
        
        # 如果是列表，进行格式化
        if isinstance(received_requests, list):
            formatted_requests = []
            for req in received_requests:
                if isinstance(req, dict):
                    formatted_requests.append({
                        "帖子ID": req.get("id", "N/A"),
                        "标题": req.get("title", "无标题"),
                        "内容": req.get("content", ""),
                        "话题": req.get("topic", ""),
                        "发布者": req.get("user_id", ""),
                        "发布时间": req.get("created_at", ""),
                        "状态": req.get("status", "")
                    })
                else:
                    # 如果列表中的元素不是字典，直接添加
                    formatted_requests.append(req)
            
            return json.dumps(formatted_requests, ensure_ascii=False, indent=2)
        
        # 其他情况，直接返回JSON格式
        return json.dumps(received_requests, ensure_ascii=False, indent=2)
        
    except Exception as e:
        return f"❌ 获取订阅需求失败: {str(e)}"

def get_user_info_ui():
    """获取当前用户信息的UI函数"""
    try:
        user_id = user_manager.get_user_id()
        
        # 获取用户的订阅
        subscriptions_response = message_client._make_sync_request(
            "GET", f"/users/{user_id}/subscriptions"
        )
        
        # 获取用户的需求
        requests_response = message_client._make_sync_request(
            "GET", f"/users/{user_id}/requests"
        )
        
        # 获取收到的需求消息
        received_requests_response = message_client._make_sync_request(
            "GET", f"/users/{user_id}/received/requests"
        )
        
        # 获取收到的应答消息
        received_responses_response = message_client._make_sync_request(
            "GET", f"/users/{user_id}/received/responses"
        )
        
        # 处理订阅数据的格式
        # 示例格式: {'status': 'success', 'user_id': 'user_a', 'subscription_count': 1, 'subscriptions': ['技术问题']}
        subscription_count = 0
        subscription_list = []
        
        if isinstance(subscriptions_response, dict):
            subscription_count = subscriptions_response.get('subscription_count', 0)
            subscription_list = subscriptions_response.get('subscriptions', [])
        elif isinstance(subscriptions_response, list):
            subscription_count = len(subscriptions_response)
            subscription_list = subscriptions_response
        
        # 处理发布的帖子数据
        # 示例格式: {"status": "success","user_id": "user_c","request_count": 1,"requests": [...]}
        published_posts_count = 0
        if isinstance(requests_response, dict):
            published_posts_count = requests_response.get('request_count', 0)
        elif isinstance(requests_response, list):
            published_posts_count = len(requests_response)
        
        # 处理收到的需求数据
        # 示例格式: {"status": "success","user_id": "user_a","message_count": 1,"requests": [...]}
        feed_posts_count = 0
        if isinstance(received_requests_response, dict):
            feed_posts_count = received_requests_response.get('message_count', 0)
        elif isinstance(received_requests_response, list):
            feed_posts_count = len(received_requests_response)
        
        # 处理收到的回复数据
        # 示例格式: {"status": "success","user_id": "user_c","message_count": 0,"responses": []}
        received_replies_count = 0
        if isinstance(received_responses_response, dict):
            received_replies_count = received_responses_response.get('message_count', 0)
        elif isinstance(received_responses_response, list):
            received_replies_count = len(received_responses_response)
        
        info = {
            "用户ID": user_id,
            "用户名": user_manager.get_display_name(),
            "加入的话题数": subscription_count,
            "发布的帖子数": published_posts_count,
            "Feed中的帖子数": feed_posts_count,
            "收到的回复数": received_replies_count,
            "订阅的话题列表": subscription_list,
            "详细数据": {
                "订阅数据": subscriptions_response,
                "发布帖子数据": requests_response,
                "Feed数据": received_requests_response,
                "收到回复数据": received_responses_response
            }
        }
        
        return json.dumps(info, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"❌ 获取用户信息失败: {str(e)}"

def get_stats_ui():
    """获取统计信息的UI函数"""
    try:
        result = message_client._make_sync_request("GET", "/stats")
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"❌ 获取统计信息失败: {str(e)}"

def test_connection_ui():
    """测试与后端服务的连接"""
    try:
        result = message_client._make_sync_request("GET", "/stats")
        return f"✅ 服务器连接正常！\n{json.dumps(result, ensure_ascii=False, indent=2)}"
    except Exception as e:
        return f"❌ 无法连接到服务器: {str(e)}\n\n请确保后端服务运行在 {BASE_URL}"

# 同步包装函数，带有更好的错误处理
def sync_subscribe_topic(topic: str):
    if not topic.strip():
        return "❌ 请输入话题名称"
    try:
        return subscribe_topic_ui(topic.strip())
    except Exception as e:
        return f"❌ 操作失败: {str(e)}"

def sync_unsubscribe_topic(topic: str):
    if not topic.strip():
        return "❌ 请输入话题名称"
    try:
        return unsubscribe_topic_ui(topic.strip())
    except Exception as e:
        return f"❌ 操作失败: {str(e)}"

def sync_get_topics():
    try:
        return get_topics_ui()
    except Exception as e:
        return f"❌ 获取topics失败: {str(e)}"

def sync_publish_request(topic: str, title: str, content: str):
    if not topic.strip() or not content.strip():
        return "❌ 请选择话题并输入帖子内容"
    try:
        return publish_request_ui(topic.strip(), title.strip(), content.strip())
    except Exception as e:
        return f"❌ 发布失败: {str(e)}"

def sync_publish_response(request_id: str, content: str):
    if not request_id.strip() or not content.strip():
        return "❌ 请输入帖子ID和回复内容"
    try:
        return publish_response_ui(request_id.strip(), content.strip())
    except Exception as e:
        return f"❌ 应答发布失败: {str(e)}"

def sync_get_my_requests():
    try:
        return get_my_requests_ui()
    except Exception as e:
        return f"❌ 获取我的需求失败: {str(e)}"

def sync_get_my_responses():
    try:
        return get_my_responses_ui()
    except Exception as e:
        return f"❌ 获取我的应答失败: {str(e)}"

def sync_get_subscribed_requests():
    try:
        return get_subscribed_requests_ui()
    except Exception as e:
        return f"❌ 获取订阅需求失败: {str(e)}"

def sync_get_user_info():
    try:
        return get_user_info_ui()
    except Exception as e:
        return f"❌ 获取用户信息失败: {str(e)}"

def sync_get_stats():
    try:
        return get_stats_ui()
    except Exception as e:
        return f"❌ 获取统计信息失败: {str(e)}"

def sync_test_connection():
    try:
        return test_connection_ui()
    except Exception as e:
        return f"❌ 连接测试失败: {str(e)}"
def get_available_post_ids():
    """获取用户Feed中可用的帖子ID列表，用于自动补全"""
    try:
        user_id = user_manager.get_user_id()
        received_requests_response = message_client._make_sync_request(
            "GET", f"/users/{user_id}/received/requests"
        )
        
        post_ids = []
        if isinstance(received_requests_response, dict):
            requests_list = received_requests_response.get('requests', [])
            for req in requests_list:
                if isinstance(req, dict):
                    post_id = req.get("request_id")
                    if post_id:
                        post_ids.append(post_id)
        elif isinstance(received_requests_response, list):
            for req in received_requests_response:
                if isinstance(req, dict):
                    post_id = req.get("id", req.get("request_id"))
                    if post_id:
                        post_ids.append(post_id)
        
        return post_ids
    except Exception as e:
        logger.error(f"获取帖子ID列表失败: {e}")
        return []

def validate_post_id_in_subscribed_topics(post_id: str):
    """验证帖子ID是否在用户关注的话题中"""
    try:
        user_id = user_manager.get_user_id()
        received_requests_response = message_client._make_sync_request(
            "GET", f"/users/{user_id}/received/requests"
        )
        
        # 检查帖子ID是否在用户的Feed中
        if isinstance(received_requests_response, dict):
            requests_list = received_requests_response.get('requests', [])
            for req in requests_list:
                if isinstance(req, dict):
                    if req.get("request_id") == post_id:
                        return True, req.get("topic", ""), req.get("content", "")[:50] + "..."
        elif isinstance(received_requests_response, list):
            for req in received_requests_response:
                if isinstance(req, dict):
                    if req.get("id") == post_id or req.get("request_id") == post_id:
                        return True, req.get("topic", ""), req.get("content", "")[:50] + "..."
        
        return False, "", ""
    except Exception as e:
        logger.error(f"验证帖子ID失败: {e}")
        return False, "", ""

def sync_publish_response_with_validation(request_id: str, content: str):
    """带验证的回复发布函数"""
    if not request_id.strip() or not content.strip():
        return "❌ 请输入帖子ID和回复内容"
    
    # 验证帖子ID是否在用户关注的话题中
    is_valid, topic, post_preview = validate_post_id_in_subscribed_topics(request_id.strip())
    
    if not is_valid:
        return f"❌ 帖子ID '{request_id}' 不在你关注的话题中，或者帖子不存在。请检查ID是否正确，或者确保你已经订阅了相关话题。"
    
    try:
        result = publish_response_ui(request_id.strip(), content.strip())
        return f"✅ 验证通过！正在回复话题 '{topic}' 中的帖子\n预览: {post_preview}\n\n{result}"
    except Exception as e:
        return f"❌ 回复发布失败: {str(e)}"

# 创建Gradio界面
def create_gradio_interface():
    """创建Gradio Web界面"""
    
    with gr.Blocks(title="社区论坛 - MCP", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# 🔥 社区论坛")
        gr.Markdown("欢迎来到我们的社区！在这里你可以订阅感兴趣的话题，发布帖子，回复讨论")
        
        # 用户信息和连接测试区域
        with gr.Row():
            with gr.Column(scale=2):
                gr.Markdown(f"**用户**: {user_manager.get_display_name()} (`{user_manager.get_user_id()[:8]}...`)")
                gr.Markdown(f"**服务器状态**: `{BASE_URL}`")
            with gr.Column(scale=1):
                test_conn_btn = gr.Button("🔗 检查连接", variant="secondary", size="sm")
        
        conn_status = gr.Textbox(
            label="服务器状态",
            lines=3,
            interactive=False,
            visible=False
        )
        
        def toggle_connection_status():
            return gr.update(visible=True)
        
        def show_connection_test():
            result = sync_test_connection()
            return result, gr.update(visible=True)
        
        test_conn_btn.click(
            fn=show_connection_test,
            outputs=[conn_status, conn_status]
        )
        
        with gr.Tabs():
            # 话题管理标签页
            with gr.TabItem("🏠 话题"):
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### 浏览所有话题")
                        topics_btn = gr.Button("🔍 发现话题", variant="primary")
                        topics_output = gr.Textbox(
                            label="可用的话题", 
                            lines=10, 
                            interactive=False
                        )
                        topics_btn.click(sync_get_topics, outputs=topics_output)
                    
                    with gr.Column():
                        gr.Markdown("### 加入/离开社区")
                        gr.Markdown(f"*当前用户: {user_manager.get_user_id()[:8]}...*")
                        topic_sub = gr.Textbox(label="话题名称", placeholder="输入话题名称")
                        
                        with gr.Row():
                            subscribe_btn = gr.Button("🔔 加入", variant="primary")
                            unsubscribe_btn = gr.Button("🔕 离开", variant="secondary")
                        
                        sub_output = gr.Textbox(
                            label="操作结果", 
                            lines=5, 
                            interactive=False
                        )
                        
                        subscribe_btn.click(
                            sync_subscribe_topic,
                            inputs=topic_sub,
                            outputs=sub_output
                        )
                        unsubscribe_btn.click(
                            sync_unsubscribe_topic,
                            inputs=topic_sub,
                            outputs=sub_output
                        )
            
            # 发帖和回复标签页
            with gr.TabItem("✍️ 发帖&回复"):
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### 📝 创建新帖子")
                        gr.Markdown(f"*发帖用户: {user_manager.get_user_id()[:8]}...*")
                        topic_req = gr.Textbox(label="选择话题", placeholder="输入话题名称")
                        title_req = gr.Textbox(label="帖子标题", placeholder="给你的帖子起个吸引人的标题")
                        content_req = gr.Textbox(
                            label="帖子内容", 
                            placeholder="分享你的想法、问题或故事...",
                            lines=4
                        )
                        publish_req_btn = gr.Button("🚀 发布帖子", variant="primary")
                        req_output = gr.Textbox(
                            label="发布状态", 
                            lines=4, 
                            interactive=False
                        )
                        
                        publish_req_btn.click(
                            sync_publish_request,
                            inputs=[topic_req, title_req, content_req],
                            outputs=req_output
                        )
                    
                    with gr.Column():
                        gr.Markdown("### 🔥 热门帖子")
                        gr.Markdown("*来自你关注的话题*")
                        refresh_subscribed_btn = gr.Button("🔄 刷新Feed", variant="secondary")
                        subscribed_requests_output = gr.Textbox(
                            label="你的个人Feed", 
                            lines=12, 
                            interactive=False
                        )
                        
                        refresh_subscribed_btn.click(
                            sync_get_subscribed_requests,
                            outputs=subscribed_requests_output
                        )
                    
                    with gr.Column():
                        gr.Markdown("### 💬 回复帖子")
                        gr.Markdown(f"*回复用户: {user_manager.get_user_id()[:8]}...*")
                        gr.Markdown("💡 *选择要回复的帖子，或手动输入帖子ID*")
                        
                        # 刷新可选帖子按钮
                        refresh_posts_btn = gr.Button("🔄 刷新可回复的帖子", variant="secondary")
                        
                        # 帖子选择下拉框
                        post_selector = gr.Dropdown(
                            label="选择要回复的帖子",
                            choices=[],
                            value=None,
                            interactive=True,
                            allow_custom_value=True,
                            info="选择帖子或输入自定义ID"
                        )
                        
                        # 选中帖子的详细信息显示
                        selected_post_info = gr.Textbox(
                            label="选中帖子信息", 
                            lines=3, 
                            interactive=False,
                            visible=False
                        )
                        
                        content_resp = gr.Textbox(
                            label="你的回复", 
                            placeholder="写下你的想法和观点...",
                            lines=6
                        )
                        
                        publish_resp_btn = gr.Button("💬 发表回复", variant="primary")
                        
                        resp_output = gr.Textbox(
                            label="回复状态", 
                            lines=4, 
                            interactive=False
                        )
                        
                        # 获取可回复帖子的详细信息
                        def get_available_posts_with_info():
                            """获取可回复帖子的详细信息，用于下拉选择"""
                            try:
                                user_id = user_manager.get_user_id()
                                received_requests_response = message_client._make_sync_request(
                                    "GET", f"/users/{user_id}/received/requests"
                                )
                                
                                posts_info = []
                                choices = []
                                
                                if isinstance(received_requests_response, dict):
                                    requests_list = received_requests_response.get('requests', [])
                                    for req in requests_list:
                                        if isinstance(req, dict):
                                            post_id = req.get("request_id")
                                            topic = req.get("topic", "")
                                            content = req.get("content", "")
                                            publisher = req.get("publisher_user_id", "")
                                            
                                            if post_id:
                                                # 创建显示标签（截取内容前30个字符）
                                                content_preview = content[:30] + "..." if len(content) > 30 else content
                                                display_label = f"[{topic}] {content_preview} - by {publisher[:8]}..."
                                                choices.append((display_label, post_id))
                                                posts_info.append({
                                                    "id": post_id,
                                                    "topic": topic,
                                                    "content": content,
                                                    "publisher": publisher
                                                })
                                
                                elif isinstance(received_requests_response, list):
                                    for req in received_requests_response:
                                        if isinstance(req, dict):
                                            post_id = req.get("id", req.get("request_id"))
                                            topic = req.get("topic", "")
                                            content = req.get("content", "")
                                            publisher = req.get("user_id", req.get("publisher_user_id", ""))
                                            
                                            if post_id:
                                                content_preview = content[:30] + "..." if len(content) > 30 else content
                                                display_label = f"[{topic}] {content_preview} - by {publisher[:8]}..."
                                                choices.append((display_label, post_id))
                                                posts_info.append({
                                                    "id": post_id,
                                                    "topic": topic,
                                                    "content": content,
                                                    "publisher": publisher
                                                })
                                
                                return choices, posts_info
                            except Exception as e:
                                logger.error(f"获取可回复帖子失败: {e}")
                                return [], []
                        
                        # 刷新帖子列表
                        def refresh_posts():
                            choices, posts_info = get_available_posts_with_info()
                            if choices:
                                return gr.update(choices=choices, value=None)
                            else:
                                return gr.update(choices=[("暂无可回复的帖子", "")], value=None)
                        
                        # 显示选中帖子的详细信息
                        def show_selected_post_info(selected_post_id):
                            if not selected_post_id:
                                return gr.update(visible=False)
                            
                            is_valid, topic, preview = validate_post_id_in_subscribed_topics(selected_post_id)
                            if is_valid:
                                info_text = f"话题: {topic}\n内容预览: {preview}"
                                return gr.update(value=info_text, visible=True)
                            else:
                                info_text = f"❌ 帖子ID无效或不在你关注的话题中"
                                return gr.update(value=info_text, visible=True)
                        
                        # 绑定事件
                        refresh_posts_btn.click(
                            refresh_posts,
                            outputs=post_selector
                        )
                        
                        post_selector.change(
                            show_selected_post_info,
                            inputs=post_selector,
                            outputs=selected_post_info
                        )
                        
                        publish_resp_btn.click(
                            sync_publish_response_with_validation,
                            inputs=[post_selector, content_resp],
                            outputs=resp_output
                        )
            
            # 我的活动标签页
            with gr.TabItem("📊 我的活动"):
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### 📝 我的帖子")
                        refresh_requests_btn = gr.Button("🔄 查看我的帖子", variant="primary")
                        my_requests_output = gr.Textbox(
                            label="我发布的帖子", 
                            lines=12, 
                            interactive=False
                        )
                        
                        refresh_requests_btn.click(
                            sync_get_my_requests,
                            outputs=my_requests_output
                        )
                    
                    with gr.Column():
                        gr.Markdown("### 💬 收到的回复")
                        refresh_responses_btn = gr.Button("🔄 查看回复通知", variant="primary")
                        my_responses_output = gr.Textbox(
                            label="我收到的回复", 
                            lines=12, 
                            interactive=False
                        )
                        
                        refresh_responses_btn.click(
                            sync_get_my_responses,
                            outputs=my_responses_output
                        )
            
            # 用户资料标签页
            with gr.TabItem("👤 我的资料"):
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### 🏆 我的个人档案")
                        get_info_btn = gr.Button("查看我的档案", variant="primary")
                        user_info_output = gr.Textbox(
                            label="我的个人档案", 
                            lines=15, 
                            interactive=False
                        )
                        
                        get_info_btn.click(
                            sync_get_user_info,
                            outputs=user_info_output
                        )
                    
                    with gr.Column():
                        gr.Markdown("### 📈 社区统计")
                        stats_btn = gr.Button("查看社区数据", variant="primary")
                        stats_output = gr.Textbox(
                            label="社区统计", 
                            lines=15, 
                            interactive=False
                        )
                        
                        stats_btn.click(sync_get_stats, outputs=stats_output)
        
        gr.Markdown("---")
        gr.Markdown("💡 **欢迎来到社区论坛**")
    
    return demo

async def main():
   app = create_gradio_interface()
   # 启动界面（阻塞）
   app.launch(mcp_server=True, server_port=7860)
   
if __name__ == "__main__":
    asyncio.run(main())