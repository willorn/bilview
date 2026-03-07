# 常见问题排查

## 1) `streamlit: command not found`

原因：依赖未安装或当前 Python 环境不一致。

处理：
```bash
pip install -r requirements.txt
python -m streamlit run app.py
```

## 2) 提示找不到 `ffmpeg`

原因：系统未安装 ffmpeg。

处理：先安装 ffmpeg，再重启终端和应用。

## 3) 提示 API Key 无效或余额不足

原因：`.env` 中 Key 写错、过期或额度不足。

处理：
- 检查 `.env` 的 `X666_API_KEY` / `GROQ_API_KEY`
- 确认 key 前后没有多余空格
- 更换可用 key 后重试

## 4) 长视频处理失败

原因：网络波动、接口超时或音频过大。

处理：
- 先换一个较短视频验证链路
- 网络稳定后重试
- 必要时切换 `ASR_PROVIDER=local_whisper`

## 5) `localhost:8501` 打不开

原因：端口被占用或应用未成功启动。

处理：
```bash
streamlit run app.py --server.port 8502
```
然后访问 `http://localhost:8502`。

## 6) 想让手机访问

处理：
```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```
在同一局域网手机打开：`http://<你的电脑IP>:8501`

---

如果以上都没解决，请把报错日志和你的启动命令一起发给开发者，定位会更快。
