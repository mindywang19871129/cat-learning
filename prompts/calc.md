请执行每日数学计算专项出题。

⚠️ send_feishu已被系统禁用，只存储文件！

test_id = "{test_id}"
每道题用 _gen_question_id() 生成全局唯一编号

出题要求（纯计算，10题，适合小学生）：
- 两位数×一位数：2题（如 47×6=）
- 三位数÷一位数：2题（如 258÷3=）
- 三位数加减法：2题（如 456+278=, 803-267=）
- 四则混合运算：2题（如 25+36÷6=, (45-18)×3=）
- 连乘/连除：2题（如 12×3×2=, 96÷4÷2=）
- ⚠️ 纯计算题，不需要应用题，不需要图形
- 每道题给出标准答案

用 write_file 存入 data/questions/questions_{today_str}_calc.json
格式：{{"test_id":"{test_id}","date":"{today_str}","type":"calc","questions":[{{id,question,answer}}]}}
