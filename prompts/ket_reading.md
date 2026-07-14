请执行KET阅读真题训练（暑假模式，对标真实考试）。

⚠️⚠️⚠️ 严禁调用 send_feishu！系统已被配置为拒绝所有 send_feishu 调用！

test_id = "{test_id}"
每道题用 _gen_question_id() 生成全局唯一编号

1. 读取 KET备考计划.md → 确认当前阶段（第二阶段·能力提升）
2. 读取 data/ket_vocabulary.json → 确保词汇在KET词表内
3. 读取 data/error_book.json → 筛选英语错题

按当天日期取模确定题型组合：
- 日期%3==0：短信息匹配3题(Part1) + 长文选择3题(Part3)
- 日期%3==1：短文匹配3题(Part2) + 完形填空3题(Part4)
- 日期%3==2：开放式完形3题(Part5) + 长文选择3题(Part3)

⚠️⚠️⚠️ 【核心流程·逐题出题】每次只生成并发送1道题，绝不能一次出6题！

```
① 用 call_llm 生成第1题（含完整原文）→ send_feishu(msg_type="text") 发送第1题
② 用 call_llm 生成第2题（含完整原文）→ send_feishu(msg_type="text") 发送第2题
③ 用 call_llm 生成第3题（含完整原文）→ send_feishu(msg_type="text") 发送第3题
④ 用 call_llm 生成第4题（含完整原文）→ send_feishu(msg_type="text") 发送第4题
⑤ 用 call_llm 生成第5题（含完整原文）→ send_feishu(msg_type="text") 发送第5题
⑥ 用 call_llm 生成第6题（含完整原文）→ send_feishu(msg_type="text") 发送第6题
⑦ 把所有6题结果组装成 questions 数组，write_file 存入 {output_file}
```

每次 call_llm 的 prompt：
"请生成一道KET阅读真题（只生成1道题，不要生成多道）：
题型：{Part1/2/3/4/5}
词汇范围：KET核心词表 ~1500词
输出JSON格式：{{"id":"","question":"完整原文+题目指令","answer":"","hint":"","ket_part":"","topic_id":""}}
⚠️ question字段必须包含完整原文，禁止用...截断！"

⚠️⚠️⚠️ 格式要求：
- 每道题的question字段必须包含完整原文（告示全文/短文全文/长文全文/完形全文）
- Part1：完整告示/通知/邮件原文 + 匹配题
- Part2：3篇完整短文原文（每篇50-80词）+ 匹配题
- Part3：完整长文原文（150-200词）+ 选择题
- Part4/5：完整文章，空格用______（6个下划线），选项列在题号下方
- 选项用A/B/C
- 每道题标注KET真题Part编号
- 用 send_feishu(msg_type="text") 文本消息发送，禁止用卡片消息

最终存入格式：{{"test_id":"{test_id}","date":"{today_str}","type":"ket_reading","questions":[6道题]}}

⚠️ 铁律：每次只生成1道题，发完再生成下一道！