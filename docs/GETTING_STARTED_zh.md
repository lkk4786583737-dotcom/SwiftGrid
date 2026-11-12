# 本地启动与验证

[返回首页](../README.md) · [演示数据](../examples/quarterly-sales/README.md) · [部署选择](DEPLOYMENT_zh.md)

需要 Python 3.11+、Node.js 24+，以及可用的 OpenAI-compatible 模型接口。以下 PowerShell 命令从仓库根目录开始。

## 安装与配置

```powershell
python -m venv .venv
.venv\Scripts\pip.exe install -r requirements.txt
cd frontend
npm ci
cd ..
```

可在页面的模型配置中填入密钥、服务地址与模型名；也可在启动后端的终端设置环境变量。将示例值替换为自己的配置，勿提交真实密钥：

```powershell
$env:SWIFTGRID_API_KEY = "your-key"
$env:SWIFTGRID_BASE_URL = "https://api.deepseek.com"
$env:SWIFTGRID_MODEL = "your-available-model"
```

`.env.example` 列出支持的变量；不要假设创建 `.env` 就会被所有启动方式自动加载。学习和修复会调用所配置的模型并产生费用；离线核对案例不调用模型。

## 同源运行：打开 localhost:8000

```powershell
cd frontend
npm run build
cd ..
.venv\Scripts\python.exe -m uvicorn swiftgrid.backend.main:app --host 127.0.0.1 --port 8000
```

FastAPI 自动识别 `frontend/out`，打开 `http://localhost:8000`。同源构建使用默认的相对 API 地址；如曾设置 `NEXT_PUBLIC_API_BASE`，先移除跨域开发覆盖再构建。

## 前后端开发模式

终端 1：

```powershell
.venv\Scripts\python.exe -m uvicorn swiftgrid.backend.main:app --host 127.0.0.1 --port 8000 --reload
```

在 `frontend/.env.local` 中设置：

```env
NEXT_PUBLIC_API_BASE=http://localhost:8000
NEXT_PUBLIC_MOCK=false
```

终端 2：

```powershell
cd frontend
npm run dev
```

打开 `http://localhost:3000`。按 [案例说明](../examples/quarterly-sales/README.md) 上传学习源表和目标，保存后用 B、C 两期源表复用；对应标准结果仅用于事后核对。

## Docker

以下为 Bash 示例；在终端先设置 `SWIFTGRID_API_KEY`，避免将密钥写入镜像或仓库：

```bash
docker build -t swiftgrid .
docker run --rm -p 8000:8000 \
  -e SWIFTGRID_API_KEY \
  -e SWIFTGRID_MODEL=your-available-model \
  -e SWIFTGRID_BASE_URL=https://api.deepseek.com \
  -e SWIFTGRID_ALLOW_RUNTIME_CONFIG=false \
  -v swiftgrid-data:/app/data \
  swiftgrid
```

`SWIFTGRID_ALLOW_RUNTIME_CONFIG=false` 禁用浏览器修改全进程模型配置；它不等于账号认证或租户隔离。当前仅适合受控的单用户环境。`data/` 保存逻辑及学习数据，部署时需要保护并持久化。

## 验证

```powershell
.venv\Scripts\python.exe examples\quarterly-sales\verify_results.py
.venv\Scripts\python.exe -m py_compile engine.py storage.py swiftgrid\backend\main.py
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_modeling_multi_agent_*.py" -v
.venv\Scripts\python.exe -m unittest tests/test_engine_guardrail.py -v
cd frontend
npm run typecheck
npm run build
```

案例核对会同时报告数据结果与工作表名称差异；离线测试通过不代表真实模型在任意任务上通过。详细数据与执行边界见 [架构文档](ARCHITECTURE_zh.md)。
