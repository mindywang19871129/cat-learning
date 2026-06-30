请执行每日英语语法专项出题。

⚠️ send_feishu已被系统禁用，只存储文件！

test_id = "{test_id}"
每道题用 _gen_question_id() 生成全局唯一编号

1. 先读 KET备考计划.md 确认当前语法范围
2. 再读 root.md「KET题型格式模板」确认格式要求
3. 从 knowledge_map.json 的 grammar 部分选 1-2 个语法点

出题（4题，KET A2标准）：
- 时态填空：1题（选择正确的动词形式）
- 改错：1题（找出句子中的语法错误并改正，标注错误位置）
- 句型转换：1题（如肯定句变否定句、陈述句变疑问句）
- 完形填空：1题（含完整原文+选项，至少3空）
- ⚠️ 填空/改错必须包含完整原文和选项！
- 每道题用具体数字和字母，禁止△●等符号
- 每道题给出完整标准答案和语法解析

用 write_file 存入 data/questions/questions_{today_str}_grammar.json
格式：{{"test_id":"{test_id}","date":"{today_str}","type":"grammar","questions":[{{id,question,answer,hint,topic_id}}]}}
