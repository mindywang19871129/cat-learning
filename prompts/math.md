请执行每日数学综合出题。

⚠️⚠️⚠️ 铁则：你只能出数学题，不能出任何英语题！英语归单独的语法/作文任务管！
⚠️⚠️⚠️ 严禁调用 send_feishu！系统已被配置为拒绝所有 send_feishu 调用！

test_id = "{test_id}"
每道题用 _gen_question_id() 生成全局唯一编号（如 Q000015, Q000016...）

1. 读取 data/mastery.json → 找到薄弱知识点（score<95的先出）
2. 读取 data/error_book.json → 按艾宾浩斯检查今天该复习的错题
3. 读取 data/adjustments.json → 获取题数/难度设置
4. 读取 data/knowledge_map.json → 只看 math 部分
5. 读取 root.md「自适应难度系统」→ 根据上次成绩调整难度

出题（4道数学题，7单元均衡分布）：
- ⚠️ 7个单元均衡出题，禁止集中在一个单元！
- 单元分布：乘法1题 + 除法1题 + 周长1题 + 图形运动/数据/实践活动轮换1题
- 第五单元（关系与规律）最多1题，不超过25%
- 包含：计算应用40% + 周长/图形30% + 单位换算20% + 规律推理10%
- ⚠️ 禁用具体水果名！用「圆形」「三角形」「正方形」或直接给数字
- ⚠️ 题目标注必须用具体数字（如24、36），不能出现△△△或●●●等符号
- 每道题至少需要2步推理
- 每道题给出完整标准答案和详细解题思路
- 难度默认hard，如有NEEDS_REVIEW则降为normal

用 write_file 存入 data/today_questions.json
格式：{{"test_id":"{test_id}","date":"{today_str}","math":[{{id,question,answer,hint,difficulty,topic_id}}],"english":[]}}
同时用 write_file 存入 data/questions/questions_{today_str}.json 历史存档

⚠️ 只存储，不要调用 send_feishu！（已被系统禁用）
