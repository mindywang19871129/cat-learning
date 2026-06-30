请执行每日英语作文出题。

⚠️ send_feishu已被系统禁用，只存储文件！

test_id = "{test_id}"
每道题用 _gen_question_id() 生成全局唯一编号

1. 先读 KET备考计划.md 确认当前写作要求
2. 从以下类型中轮换选择：写邮件/描述图片/讲故事/写便条

出题（1篇，KET标准）：
- 给出明确的写作任务和场景
- 要求：≥35词短文
- 提供评分要点（内容完整/语法准确/词汇恰当/格式规范）
- 提供参考范文和常用句型
- 难度对齐KET真题

用 write_file 存入 data/questions/questions_{today_str}_writing.json
格式：{{"test_id":"{test_id}","date":"{today_str}","type":"writing","prompt":"写作题目描述","reference":"范文","scoring_points":["评分点1","评分点2"]}}
