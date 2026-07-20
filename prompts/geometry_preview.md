请执行几何探索任务（暑假模式，四上线与角+方向与位置）。

⚠️⚠️⚠️ 严禁调用 send_feishu！系统已被配置为拒绝所有 send_feishu 调用！

test_id = "{test_id}"
每道题用 _gen_question_id() 生成全局唯一编号

1. 读取 data/knowledge_map_4th.json → 找 math-4a-6 到 math-4a-10（线与角）和 math-4a-18 到 math-4a-20（方向与位置）
2. 读取 data/mastery.json → 找到未掌握的几何知识点

出题（2题，每题配图，难度basic）：

题1：线与角（1题，选择题）
- 用 draw_geometry 画图：
  {{"type":"custom","code":"画一个30°锐角、一个90°直角、一个120°钝角，标注角度和名称"}}
- 题目格式（选择题，学生直接回复字母）：
  请观察图中三个角，回答以下问题：
  （1）图中∠A是锐角，∠B是直角，∠C是钝角，对吗？
  A. 对  B. 错
  （2）量角器的中心点应该对准角的哪个位置？
  A. 角的顶点  B. 角的一条边  C. 角的内部  D. 角的开口方向
- 答案格式：{{"answer":"(1) A (2) A"}}

题2：方向与位置（1题，选择题）
- 用 draw_geometry 画网格图：
  {{"type":"grid","rows":6,"cols":6,"label":"学校周边地图"}}
- 在图中标注几个点（学校在(2,3)、公园在(5,4)、书店在(1,6)）
- 题目格式（选择题，学生直接回复字母）：
  观察网格图，回答以下问题：
  （1）学校的位置用数对表示是？
  A. (2,3)  B. (3,2)  C. (5,4)  D. (1,6)
  （2）从学校(2,3)到公园(5,4)，应该怎么走？
  A. 向右3格，向上1格  B. 向右3格，向下1格  C. 向左3格，向上1格  D. 向左3格，向下1格
- 答案格式：{{"answer":"(1) A (2) A"}}

⚠️ 铁则：
- 所有题目必须用选择题格式（A/B/C/D），学生直接回复字母
- 答案格式统一为：{{"answer":"(1) X (2) Y"}}
- draw_geometry 参数必须用具体数字
- label 用中文描述
- 每道题必须包含 question 和 answer 字段

用 write_file 存入 {output_file}
格式：{{"test_id":"{test_id}","date":"{today_str}","type":"geometry_preview","questions":[{{id,question,answer,hint,image_path:"draw_geometry生成的图片路径",difficulty:"basic",topic_id}}]}}

⚠️ 只存储，不要调用 send_feishu！（已被系统禁用）