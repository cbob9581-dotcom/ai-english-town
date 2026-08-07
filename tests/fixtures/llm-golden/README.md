# llm-golden

`llm-smoke.py` 用真 key 跑 20 次后落盘的实际测量（TTFT / 总延迟 / token），
作为 `llm_total_timeout_*` / `llm_max_tokens_*` 定值依据 + mock 文本模板来源。
文件名带日期；不入 CI。
