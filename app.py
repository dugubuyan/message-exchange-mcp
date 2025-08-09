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

class UserManager:
    """用户管理器，处理动态用户ID"""
    
    def __init__(self):
        self.default_user_id = None
        self.session_users = {}  # 临时存储会话用户信息
    
    def get_or_create_user_id(self, provided_user_id: str = None) -> str:
        """获取或创建用户ID"""
        if provided_user_id:
            # 如果agent提供了user_id，直接使用
            if provided_user_id not in self.session_users:
                self.session_users[provided_user_id] = {
                    'user_id': provided_user_id,
                    'created_at': time.time(),
                    'display_name': f"用户_{provided_user_id[:8]}"
                }
                logger.info(f"注册新用户: {provided_user_id}")
            return provided_user_id
        else:
            # 如果没有提供user_id，生成一个新的
            if not self.default_user_id:
                self.default_user_id = str(uuid.uuid4())
                self.session_users[self.default_user_id] = {
                    'user_id': self.default_user_id,
                    'created_at': time.time(),
                    'display_name': f"临时用户_{self.default_user_id[:8]}"
                }
                logger.info(f"生成临时用户ID: {self.default_user_id}")
            return self.default_user_id
    
    def get_user_info(self, user_id: str = None) -> dict:
        """获取用户信息"""
        target_user_id = user_id or self.default_user_id
        if target_user_id and target_user_id in self.session_users:
            return self.session_users[target_user_id]
        else:
            # 如果用户不存在，创建一个临时的
            return {
                'user_id': target_user_id or 'unknown',
                'created_at': time.time(),
                'display_name': f"未知用户_{(target_user_id or 'unknown')[:8]}"
            }
    
    def get_display_name(self, user_id: str = None) -> str:
        """获取用户显示名称"""
        user_info = self.get_user_info(user_id)
        return user_info.get('display_name', f"用户_{(user_id or 'unknown')[:8]}")

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

# Gradio界面函数（使用同步请求和动态用户ID）
def subscribe_topic_ui(topic: str, user_id: str = None):
    """订阅topic的UI函数"""
    try:
        actual_user_id = user_manager.get_or_create_user_id(user_id)
        result = message_client._make_sync_request(
            "POST", "/topics/subscribe",
            json={"user_id": actual_user_id, "topic": topic}
        )
        return f"🎉 用户 {actual_user_id[:8]}... 成功加入{topic}话题! {json.dumps(result, ensure_ascii=False, indent=2)}"
    except Exception as e:
        return f"❌ 加入社区失败: {str(e)}"

def unsubscribe_topic_ui(topic: str, user_id: str = None):
    """取消订阅topic的UI函数"""
    try:
        actual_user_id = user_manager.get_or_create_user_id(user_id)
        result = message_client._make_sync_request(
            "POST", "/topics/unsubscribe",
            json={"user_id": actual_user_id, "topic": topic}
        )
        return f"👋 用户 {actual_user_id[:8]}... 已离开{topic}话题 {json.dumps(result, ensure_ascii=False, indent=2)}"
    except Exception as e:
        return f"❌ 离开社区失败: {str(e)}"

def get_topics_ui():
    """获取所有topics的UI函数"""
    try:
        result = message_client._make_sync_request("GET", "/topics")
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"❌ 获取topics失败: {str(e)}"

def publish_request_ui(topic: str, title: str, content: str, user_id: str = None):
    """发布需求消息的UI函数"""
    try:
        actual_user_id = user_manager.get_or_create_user_id(user_id)
        payload = {
            "user_id": actual_user_id,
            "topic": topic,
            "content": content
        }
        if title.strip():
            payload["title"] = title
        
        result = message_client._make_sync_request(
            "POST", "/requests/publish", json=payload
        )
        return f"🚀 用户 {actual_user_id[:8]}... 帖子发布成功! {json.dumps(result, ensure_ascii=False, indent=2)}"
    except Exception as e:
        return f"❌ 发布失败: {str(e)}"

def publish_response_ui(request_id: str, content: str, user_id: str = None):
    """发布应答消息的UI函数"""
    try:
        actual_user_id = user_manager.get_or_create_user_id(user_id)
        result = message_client._make_sync_request(
            "POST", "/responses/publish",
            json={
                "user_id": actual_user_id,
                "request_id": request_id,
                "content": content
            }
        )
        return f"💬 用户 {actual_user_id[:8]}... 回复发布成功! {json.dumps(result, ensure_ascii=False, indent=2)}"
    except Exception as e:
        return f"❌ 应答发布失败: {str(e)}"

def get_my_requests_ui(user_id: str = None):
    """获取我发布的需求消息"""
    try:
        actual_user_id = user_manager.get_or_create_user_id(user_id)
        requests = message_client._make_sync_request(
            "GET", f"/users/{actual_user_id}/requests"
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

def get_my_responses_ui(user_id: str = None):
    """获取我收到的应答消息"""
    try:
        actual_user_id = user_manager.get_or_create_user_id(user_id)
        responses = message_client._make_sync_request(
            "GET", f"/users/{actual_user_id}/received/responses"
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

def get_subscribed_requests_ui(user_id: str = None):
    """获取我订阅的topic收到的需求消息"""
    try:
        actual_user_id = user_manager.get_or_create_user_id(user_id)
        received_requests = message_client._make_sync_request(
            "GET", f"/users/{actual_user_id}/received/requests"
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

def get_user_info_ui(user_id: str = None):
    """获取当前用户信息的UI函数"""
    try:
        actual_user_id = user_manager.get_or_create_user_id(user_id)
        
        # 获取用户的订阅
        subscriptions_response = message_client._make_sync_request(
            "GET", f"/users/{actual_user_id}/subscriptions"
        )
        
        # 获取用户的需求
        requests_response = message_client._make_sync_request(
            "GET", f"/users/{actual_user_id}/requests"
        )
        
        # 获取收到的需求消息
        received_requests_response = message_client._make_sync_request(
            "GET", f"/users/{actual_user_id}/received/requests"
        )
        
        # 获取收到的应答消息
        received_responses_response = message_client._make_sync_request(
            "GET", f"/users/{actual_user_id}/received/responses"
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
            "用户ID": actual_user_id,
            "用户名": user_manager.get_display_name(actual_user_id),
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
def get_available_post_ids(user_id: str = None):
    """获取用户Feed中可用的帖子ID列表，用于自动补全"""
    try:
        actual_user_id = user_manager.get_or_create_user_id(user_id)
        received_requests_response = message_client._make_sync_request(
            "GET", f"/users/{actual_user_id}/received/requests"
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

def validate_post_id_in_subscribed_topics(post_id: str, user_id: str = None):
    """验证帖子ID是否在用户关注的话题中"""
    try:
        actual_user_id = user_manager.get_or_create_user_id(user_id)
        received_requests_response = message_client._make_sync_request(
            "GET", f"/users/{actual_user_id}/received/requests"
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
                gr.Markdown("**用户**: 动态分配 (由 MCP agent 提供)")
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
                        gr.Markdown("*用户ID由 MCP agent 动态提供*")
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
                        gr.Markdown("*发帖用户: 由 MCP agent 动态提供*")
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
                        gr.Markdown("*回复用户: 由 MCP agent 动态提供*")
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
                                # 使用默认用户ID获取帖子信息
                                temp_user_id = user_manager.get_or_create_user_id()
                                received_requests_response = message_client._make_sync_request(
                                    "GET", f"/users/{temp_user_id}/received/requests"
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

# MCP工具定义
@server.list_tools()
async def handle_list_tools() -> List[types.Tool]:
    """列出所有可用的工具"""
    return [
        types.Tool(
            name="launch_gradio_interface",
            description="启动Gradio Web界面",
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {"type": "integer", "description": "Web界面端口号", "default": 7860},
                    "share": {"type": "boolean", "description": "是否创建公共链接", "default": False}
                },
                "required": []
            }
        ),
        types.Tool(
            name="subscribe_topic",
            description="订阅指定的topic",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "用户ID（可选，如果不提供会自动生成）"},
                    "topic": {"type": "string", "description": "要订阅的topic名称"}
                },
                "required": ["topic"]
            }
        ),
        types.Tool(
            name="unsubscribe_topic",
            description="取消订阅指定的topic",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "用户ID（可选，如果不提供会自动生成）"},
                    "topic": {"type": "string", "description": "要取消订阅的topic名称"}
                },
                "required": ["topic"]
            }
        ),
        types.Tool(
            name="get_topics",
            description="获取所有可用的topic信息",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        types.Tool(
            name="publish_request",
            description="发布需求消息",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "发布者用户ID（可选，如果不提供会自动生成）"},
                    "topic": {"type": "string", "description": "消息topic"},
                    "content": {"type": "string", "description": "消息内容"},
                    "title": {"type": "string", "description": "消息标题（可选）"}
                },
                "required": ["topic", "content"]
            }
        ),
        types.Tool(
            name="publish_response",
            description="发布回复消息",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "回复者用户ID（可选，如果不提供会自动生成）"},
                    "request_id": {"type": "string", "description": "要回复的帖子ID"},
                    "content": {"type": "string", "description": "回复内容"}
                },
                "required": ["request_id", "content"]
            }
        ),
        types.Tool(
            name="get_user_info",
            description="获取用户信息",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "用户ID（可选，如果不提供会自动生成）"}
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_my_requests",
            description="获取用户发布的帖子",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "用户ID（可选，如果不提供会自动生成）"}
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_my_responses",
            description="获取用户收到的回复",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "用户ID（可选，如果不提供会自动生成）"}
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_subscribed_requests",
            description="获取用户订阅话题的帖子",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "用户ID（可选，如果不提供会自动生成）"}
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_stats",
            description="获取系统统计信息",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
    ]

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> List[types.TextContent]:
    """处理工具调用"""
    if arguments is None:
        arguments = {}
    
    try:
        if name == "launch_gradio_interface":
            port = arguments.get("port", 7860)
            share = arguments.get("share", False)
            
            # 在新线程中启动Gradio界面
            def run_gradio():
                demo = create_gradio_interface()
                demo.launch(server_port=port, share=share, inbrowser=True)
            
            thread = threading.Thread(target=run_gradio, daemon=True)
            thread.start()
            
            return [types.TextContent(
                type="text",
                text=f"✅ Gradio界面已启动！\n访问地址: http://localhost:{port}\n{'公共链接已创建' if share else '仅本地访问'}"
            )]
            
        elif name == "subscribe_topic":
            try:
                user_id = arguments.get("user_id")
                topic = arguments["topic"]
                result = subscribe_topic_ui(topic, user_id)
                return [types.TextContent(type="text", text=result)]
            except Exception as e:
                logger.error(f"订阅话题失败: {e}")
                return [types.TextContent(type="text", text=f"订阅失败: {str(e)}")]
                
        elif name == "unsubscribe_topic":
            try:
                user_id = arguments.get("user_id")
                topic = arguments["topic"]
                result = unsubscribe_topic_ui(topic, user_id)
                return [types.TextContent(type="text", text=result)]
            except Exception as e:
                logger.error(f"取消订阅失败: {e}")
                return [types.TextContent(type="text", text=f"取消订阅失败: {str(e)}")]
            
        elif name == "get_topics":
            try:
                result = get_topics_ui()
                return [types.TextContent(type="text", text=result)]
            except Exception as e:
                logger.error(f"获取话题失败: {e}")
                return [types.TextContent(type="text", text=f"获取话题失败: {str(e)}")]
            
        elif name == "publish_request":
            try:
                user_id = arguments.get("user_id")
                topic = arguments["topic"]
                content = arguments["content"]
                title = arguments.get("title", "")
                result = publish_request_ui(topic, title, content, user_id)
                return [types.TextContent(type="text", text=result)]
            except Exception as e:
                logger.error(f"发布帖子失败: {e}")
                return [types.TextContent(type="text", text=f"发布失败: {str(e)}")]
                
        elif name == "publish_response":
            try:
                user_id = arguments.get("user_id")
                request_id = arguments["request_id"]
                content = arguments["content"]
                result = publish_response_ui(request_id, content, user_id)
                return [types.TextContent(type="text", text=result)]
            except Exception as e:
                logger.error(f"发布回复失败: {e}")
                return [types.TextContent(type="text", text=f"回复失败: {str(e)}")]
                
        elif name == "get_user_info":
            try:
                user_id = arguments.get("user_id")
                result = get_user_info_ui(user_id)
                return [types.TextContent(type="text", text=result)]
            except Exception as e:
                logger.error(f"获取用户信息失败: {e}")
                return [types.TextContent(type="text", text=f"获取用户信息失败: {str(e)}")]
                
        elif name == "get_my_requests":
            try:
                user_id = arguments.get("user_id")
                result = get_my_requests_ui(user_id)
                return [types.TextContent(type="text", text=result)]
            except Exception as e:
                logger.error(f"获取我的帖子失败: {e}")
                return [types.TextContent(type="text", text=f"获取我的帖子失败: {str(e)}")]
                
        elif name == "get_my_responses":
            try:
                user_id = arguments.get("user_id")
                result = get_my_responses_ui(user_id)
                return [types.TextContent(type="text", text=result)]
            except Exception as e:
                logger.error(f"获取我的回复失败: {e}")
                return [types.TextContent(type="text", text=f"获取我的回复失败: {str(e)}")]
                
        elif name == "get_subscribed_requests":
            try:
                user_id = arguments.get("user_id")
                result = get_subscribed_requests_ui(user_id)
                return [types.TextContent(type="text", text=result)]
            except Exception as e:
                logger.error(f"获取订阅帖子失败: {e}")
                return [types.TextContent(type="text", text=f"获取订阅帖子失败: {str(e)}")]
            
        elif name == "get_stats":
            try:
                result = get_stats_ui()
                return [types.TextContent(type="text", text=result)]
            except Exception as e:
                logger.error(f"获取统计失败: {e}")
                # 如果外部服务不可用，返回本地统计
                local_stats = {
                    "status": "partial",
                    "message": "外部服务不可用，显示本地信息",
                    "local_stats": {
                        "mcp_server": "运行中",
                        "session_users": len(user_manager.session_users),
                        "service_status": "MCP服务运行中"
                    }
                }
                return [types.TextContent(type="text", text=json.dumps(local_stats, ensure_ascii=False, indent=2))]
        
        else:
            return [types.TextContent(type="text", text=f"未知的工具: {name}")]
        
    except Exception as e:
        logger.error(f"工具调用失败 {name}: {e}")
        return [types.TextContent(
            type="text",
            text=f"错误: {str(e)}"
        )]

async def run_mcp_server():
    """运行 MCP 服务器"""
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="gradio-message-service-mcp",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

async def run_gradio_with_mcp():
    """同时运行 Gradio 界面和 MCP 服务器"""
    # 在后台线程中启动 Gradio
    def start_gradio():
        app = create_gradio_interface()
        app.launch(server_port=7860, share=False, inbrowser=False)
    
    gradio_thread = threading.Thread(target=start_gradio, daemon=True)
    gradio_thread.start()
    
    # 等待 Gradio 启动
    await asyncio.sleep(2)
    logger.info("Gradio 界面已启动在 http://localhost:7860")
    
    # 运行 MCP 服务器（这会阻塞）
    await run_mcp_server()

def main():
    """主函数 - 根据参数决定运行模式"""
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--mcp-only":
        # 仅运行 MCP 服务器
        asyncio.run(run_mcp_server())
    elif len(sys.argv) > 1 and sys.argv[1] == "--gradio-only":
        # 仅运行 Gradio 界面
        app = create_gradio_interface()
        app.launch(server_port=7860, share=False)
    else:
        # 同时运行两者
        asyncio.run(run_gradio_with_mcp())

if __name__ == "__main__":
    main()