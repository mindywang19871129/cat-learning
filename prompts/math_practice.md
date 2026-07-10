请执行数学基础练习任务（暑假模式，紧跟概念讲解）。

⚠️⚠️⚠️ 严禁调用 send_feishu！系统已被配置为拒绝所有 send_feishu 调用！

test_id = "{test_id}"
每道题用 _gen_question_id() 生成全局唯一编号

1. 读取上一个概念讲解任务中涉及的知识点
2. 针对该知识点出3道基础题

出题（3题，难度basic）：

- 第1题：直接套用概念（如"读出下面的数：3,056,200"）
- 第2题：稍加变化（如"比较两个大数的大小"）
- 第3题：生活应用（如"小明家到学校850米，用哪个单位合适？"）

⚠️ 要求：
- 难度标记为 basic
- 每题给出完整标准答案和详细解题步骤
- 禁用具体水果名
- 用具体数字，不用△●符号

用 write_file 存入 {output_file}
格式：{{"test_id":"{test_id}","date":"{today_str}","type":"math_practice","questions":[{{id,question,answer,hint,difficulty:"basic",topic_id}}]}}

⚠️ 只存储，不要调用 send_feishu！（已被系统禁用）