# 后端手动诊断

## 首页热点 live-smoke

在后端目录执行：

```bash
cd backend && .venv/bin/python scripts/smoke_market_hotspots.py
```

脚本只读取行业、题材和一个板块成分股，并打印能力名、数据源、行数、数据时间和安全字段名，不会输出密钥或完整行情响应。公网接口出现 502、超时或风控时，脚本会返回非零状态作为诊断信号；它不属于默认 pytest 或 CI 的阻断检查。
