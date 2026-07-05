# Provider Evaluate

本仓库保存 `Provider Verify` 本地模型供应商测评站点的源码。

边界：

- API key 只在本机浏览器表单和本地 API 请求中使用，不写入仓库。
- `provider_verify_site/data/report_index.json` 是本机生成的真实测评结果，已被 `.gitignore` 排除。
- 仓库只保留 `provider_verify_site/data/report_index.example.json` 作为空模板，方便页面在没有本地结果时打开。
- 不要把 `auto_eval_runs/`、日志、缓存、真实 provider 响应、密钥文件提交到 Git。

## 本地启动

在本仓库根目录运行：

```powershell
python -m http.server 8765 --bind 127.0.0.1 -d provider_verify_site
```

另开一个终端运行本地评测 API：

```powershell
python provider_verify_site/scripts/local_eval_server.py --root . --host 127.0.0.1 --port 8766
```

然后打开：

```text
http://127.0.0.1:8765/
```

## 推荐测评流程

1. 填写 provider alias、claimed model、base URL、session-only API key、protocol。
2. 点击读取模型列表。
3. 从返回的模型下拉框选择实际要测的模型。
4. 选择完整自适应评测。
5. 开始评测。
6. 把结果理解为本地能力和 coding 证据，不把它当作官方上游身份认证。

## 验证命令

```powershell
python -m unittest discover -s provider_verify_site/tests -v
python -m py_compile provider_verify_site/scripts/task_pack_v1.py provider_verify_site/scripts/local_eval_server.py provider_verify_site/scripts/decision_aggregator_v1.py provider_verify_site/scripts/scorers_v1.py provider_verify_site/scripts/provider_runner.py provider_verify_site/scripts/build_site_data.py provider_verify_site/scripts/auto_eval_storage.py
node --check provider_verify_site/app.js
python -m json.tool provider_verify_site/data/report_index.example.json
```
