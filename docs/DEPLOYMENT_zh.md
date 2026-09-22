# SwiftGrid 部署选择

SwiftGrid 不是纯静态站点：它需要持续运行 Python/FastAPI、接收 Excel 文件、通过 SSE 推送进度，并在服务端调用模型。因此，前端静态托管平台只能承载页面，不能完整替代后端运行环境。

## 推荐顺序

| 方案 | 是否适合不断线体验 | 成本与维护 | 适用阶段 |
| --- | --- | --- | --- |
| Railway 付费容器 + Volume | 适合；需关闭 Serverless 休眠 | 配置最省，按量付费 | 当前公开演示首选 |
| 云服务器/VPS + Docker | 最可控 | 需要域名、HTTPS、监控、备份和系统维护 | 长期运行或私有部署 |
| Render 免费服务 | 不适合不断线；空闲后会休眠 | 前期便宜 | 临时验证 |
| 本机 + ngrok/Cloudflare Tunnel | 依赖本机、网络和隧道进程 | 最快，但不是稳定托管 | 开发演示 |

## Railway 部署

仓库根目录已经提供 `Dockerfile`，Railway 可以直接从 GitHub 仓库构建。

1. 在 Railway 新建 Project，并选择 **Deploy from GitHub repo**。
2. 选择 SwiftGrid 仓库；无需填写构建命令，根目录 `Dockerfile` 会被自动识别。
3. 在 Variables 添加：

   ```env
   SWIFTGRID_API_KEY=你的模型密钥
   SWIFTGRID_BASE_URL=https://api.deepseek.com
   SWIFTGRID_MODEL=deepseek-v4-pro
   SWIFTGRID_ALLOW_RUNTIME_CONFIG=false
   ```

4. 创建 Volume，并挂载到 `/app/data`，以保存逻辑库和运行工件。
5. 生成公网 Domain。
6. 若目标是“不断线”，不要启用会在无请求时休眠的 Serverless 模式。

Railway 官方资料：[Dockerfile 构建](https://docs.railway.com/builds/dockerfiles)、[Volumes](https://docs.railway.com/volumes)、[套餐与计费](https://docs.railway.com/pricing/plans)。

## VPS / 云服务器部署

这是长期最可控的方案。服务器安装 Docker 后：

```bash
git clone https://github.com/lkk4786583737-dotcom/SwiftGrid.git
cd SwiftGrid
docker build -t swiftgrid .
docker run -d --restart unless-stopped \
  --name swiftgrid \
  -p 127.0.0.1:8000:8000 \
  --env-file .env \
  -v swiftgrid-data:/app/data \
  swiftgrid
```

再使用 Caddy 或 Nginx 将域名反向代理到 `127.0.0.1:8000`，并开启 HTTPS。生产环境还应增加进程监控、磁盘备份、上传大小限制和日志轮转。

## 正式公网开放前的必做项

- 增加登录认证和用户级数据隔离；
- 将模型凭证只保存在服务端，并保持 `SWIFTGRID_ALLOW_RUNTIME_CONFIG=false`；
- 增加 IP/用户限流、单次文件大小限制、模型预算与并发上限；
- 将模型生成代码放进独立容器或受限 worker。当前 AST 护栏只能降低风险，不是安全沙箱；
- 为上传文件、运行工件和日志设置自动过期与删除策略；
- 给 `/api/health` 配置外部可用性监控。

在这些能力完成之前，更合适的方式是受限访问的作品演示，而不是匿名开放的公共 SaaS。
