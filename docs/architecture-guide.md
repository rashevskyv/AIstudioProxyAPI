# 项目架构指南

本文档详细介绍 AI Studio Proxy API 项目的模块化架构设计、组件职责和交互关系。

## 🏗️ 整体架构概览

项目采用现代化的模块化架构设计，遵循单一职责原则，确保代码的可维护性和可扩展性。

### 核心设计原则

- **模块化分离**: 按功能领域划分模块，避免循环依赖
- **单一职责**: 每个模块专注于特定的功能领域
- **配置统一**: 使用 `.env` 文件和 `config/` 模块统一管理配置
- **依赖注入**: 通过 `dependencies.py` 管理组件依赖关系
- **异步优先**: 全面采用异步编程模式，提升性能

## 📁 模块结构详解

```
AIstudioProxyAPI/
├── api_utils/              # FastAPI 应用核心模块
│   ├── app.py             # FastAPI 应用入口和生命周期管理
│   ├── routers/           # API 路由定义（按职责拆分）
│   ├── routes.py          # 兼容层：重导出 routers/* 端点
│   ├── request_processor.py # 请求处理核心逻辑
│   ├── queue_worker.py    # 异步队列工作器
│   ├── auth_utils.py      # API 密钥认证管理
│   └── dependencies.py   # FastAPI 依赖注入
├── browser_utils/          # 浏览器自动化模块
│   ├── page_controller.py # 页面控制器和生命周期管理
│   ├── model_management.py # AI Studio 模型管理
│   ├── script_manager.py  # 脚本注入管理 (v3.0)
│   ├── operations.py      # 浏览器操作封装
│   └── initialization.py # 浏览器初始化逻辑
├── config/                 # 配置管理模块
│   ├── settings.py        # 主要设置和环境变量
│   ├── constants.py       # 系统常量定义
│   ├── timeouts.py        # 超时配置管理
│   └── selectors.py       # CSS 选择器定义
├── models/                 # 数据模型定义
│   ├── chat.py           # 聊天相关数据模型
│   ├── exceptions.py     # 自定义异常类
│   └── logging.py        # 日志相关模型
├── stream/                 # 流式代理服务模块
│   ├── main.py           # 流式代理服务入口
│   ├── proxy_server.py   # 代理服务器实现
│   ├── interceptors.py   # 请求拦截器
│   └── utils.py          # 流式处理工具
├── logging_utils/          # 日志管理模块
│   └── setup.py          # 日志系统配置
└── node_stream/            # Node流式处理模块
```

## 🔧 核心模块详解

### 1. api_utils/ - FastAPI 应用核心

**职责**: FastAPI 应用的核心逻辑，包括路由、认证、请求处理等。

#### app.py - 应用入口

- FastAPI 应用创建和配置
- 生命周期管理 (startup/shutdown)
- 中间件配置 (API 密钥认证)
- 全局状态初始化

#### routers/* - API 路由（按职责拆分）

- static.py: `/`, `/webui.css`, `/webui.js`
- info.py: `/api/info`
- health.py: `/health`
- models.py: `/v1/models`
- chat.py: `/v1/chat/completions`
- queue.py: `/v1/queue`, `/v1/cancel/{req_id}`
- logs_ws.py: `/ws/logs`
- api_keys.py: `/api/keys*`

应用层从 `api_utils.routers` 导入进行注册，已移除旧的集中式 `routes.py` 文件。

#### request_processor.py - 请求处理核心

- 三层响应获取机制实现
- 流式和非流式响应处理
- 客户端断开检测
- 错误处理和重试逻辑

#### queue_worker.py - 队列工作器

- 异步请求队列处理
- 并发控制和资源管理
- 请求优先级处理

### 2. browser_utils/ - 浏览器自动化

**职责**: 浏览器自动化、页面控制、脚本注入等功能。

#### page_controller.py - 页面控制器

- Camoufox 浏览器生命周期管理
- 页面导航和状态监控
- 认证文件管理

#### script_manager.py - 脚本注入管理 (v3.0)

- Playwright 原生网络拦截
- 油猴脚本解析和注入
- 模型数据同步

#### model_management.py - 模型管理

- AI Studio 模型列表获取
- 模型切换和验证
- 排除模型处理

### 3. config/ - 配置管理

**职责**: 统一的配置管理，包括环境变量、常量、超时等。

#### settings.py - 主要设置

- `.env` 文件加载
- 环境变量解析
- 配置验证和默认值

#### constants.py - 系统常量

- API 端点常量
- 错误代码定义
- 系统标识符

### 4. stream/ - 流式代理服务

**职责**: 独立的流式代理服务，提供高性能的请求转发。

#### proxy_server.py - 代理服务器

- HTTP/HTTPS 代理实现
- 请求拦截和修改
- 上游代理支持

#### interceptors.py - 请求拦截器

- AI Studio 请求拦截
- 响应数据解析
- 流式数据处理

## 🔄 三层响应获取机制

项目实现了三层响应获取机制，确保高可用性和最佳性能：

### 第一层: 集成流式代理 (Stream Proxy)

- **位置**: `stream/` 模块
- **端口**: 3120 (可配置)
- **优势**: 最佳性能，直接处理请求
- **适用**: 日常使用，生产环境

### 第二层: 外部 Helper 服务

- **配置**: 通过 `--helper` 参数或环境变量
- **依赖**: 需要有效的认证文件
- **适用**: 备用方案，特殊环境

### 第三层: Playwright 页面交互

- **位置**: `browser_utils/` 模块
- **方式**: 浏览器自动化操作
- **优势**: 完整参数支持，最终后备
- **适用**: 调试模式，参数精确控制

## 🧭 请求处理路径（辅助流/Playwright）

- 辅助流路径（STREAM）：
  - 入口：`_handle_auxiliary_stream_response`
  - 生成器：`_gen_sse_from_aux_stream`（从 `STREAM_QUEUE` 消费，产出 OpenAI 兼容 SSE，携带 tool_calls 和 usage）
  - 适合：高性能场景，SSE 首选

- Playwright 路径（页面）：
  - 入口：`_handle_playwright_response`
  - 生成器：`_gen_sse_from_playwright`（通过 `PageController.get_response` 拉取最终文本，按行/字符分块输出，附带 usage）
  - 适合：作为回退路径，确保功能完整

两条路径均保持：
- 客户端断开检测与提前结束
- 最终使用统计 `usage` 的输出
- OpenAI 兼容的 SSE/JSON 格式

## 🔐 认证系统架构

### API 密钥管理

- **存储**: `auth_profiles/key.txt` 文件
- **格式**: 每行一个密钥
- **验证**: Bearer Token 和 X-API-Key 双重支持
- **管理**: Web UI 分级权限查看

### 浏览器认证

- **文件**: `auth_profiles/active/*.json`
- **内容**: 浏览器会话和 Cookie
- **更新**: 通过调试模式重新获取

## 📊 配置管理架构

### 配置优先级

1. **命令行参数** (最高优先级)
2. **环境变量** (`.env` 文件)
3. **默认值** (代码中定义)

### 配置分类

- **服务配置**: 端口、代理、日志等
- **功能配置**: 脚本注入、认证、超时等
- **API 配置**: 默认参数、模型设置等

## 🚀 脚本注入架构 v3.0

### 工作机制

1. **脚本解析**: 从油猴脚本解析 `MODELS_TO_INJECT` 数组
2. **网络拦截**: Playwright 拦截 `/api/models` 请求
3. **数据合并**: 将注入模型与原始模型合并
4. **响应修改**: 返回包含注入模型的完整列表
5. **前端注入**: 同时注入脚本确保显示一致

### 技术优势

- **100% 可靠**: Playwright 原生拦截，无时序问题
- **零维护**: 脚本更新自动生效
- **完全同步**: 前后端使用相同数据源

## 🔧 开发和部署

### 开发环境

- **依赖管理**: Poetry
- **类型检查**: Pyright
- **代码格式**: Black + isort
- **测试框架**: pytest

### 部署方式

- **本地部署**: Poetry 虚拟环境
- **Docker 部署**: 多阶段构建，支持多架构
- **配置管理**: 统一的 `.env` 文件

## 📈 性能优化

### 异步处理

- 全面采用 `async/await`
- 异步队列处理请求
- 并发控制和资源管理

### 缓存机制

- 模型列表缓存
- 认证状态缓存
- 配置热重载

### 资源管理

- 浏览器实例复用
- 连接池管理
- 内存优化

## 🔍 监控和调试

### 日志系统

- 分级日志记录
- WebSocket 实时日志
- 错误追踪和报告

### 健康检查

- 组件状态监控
- 队列长度监控
- 性能指标收集

这种模块化架构确保了项目的可维护性、可扩展性和高性能，为用户提供稳定可靠的 AI Studio 代理服务。
