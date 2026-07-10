请执行几何探索任务（暑假模式，四上线与角+方向与位置）。

⚠️⚠️⚠️ 严禁调用 send_feishu！系统已被配置为拒绝所有 send_feishu 调用！

test_id = "{test_id}"
每道题用 _gen_question_id() 生成全局唯一编号

1. 读取 data/knowledge_map_4th.json → 找 math-4a-6 到 math-4a-10（线与角）和 math-4a-18 到 math-4a-20（方向与位置）
2. 读取 data/mastery.json → 找到未掌握的几何知识点

出题（2题，每题配图，难度basic）：

题1：线与角（1题）
- 用 draw_geometry 画图：
  线段、射线、直线的对比图 — {{"type":"custom","code":"画一条线段AB(端点实心)、一条射线CD(一个端点)、一条直线EF(无端点)，标注名称和区别"}}
  或 角的分类图 — {{"type":"custom","code":"画一个30°锐角、一个90°直角、一个120°钝角，标注角度和名称"}}
- 题目：请指出图中哪个是锐角、直角、钝角？量角器的中心点应该对准哪里？

题2：方向与位置（1题）
- 用 draw_geometry 画网格图：
  {{"type":"grid","rows":6,"cols":6,"label":"学校周边地图"}}
- 在图中标注几个点（学校、公园、书店等）
- 题目：用数对表示学校的位置、描述从学校到公园的路线

⚠️ draw_geometry 绘图规范：
- 参数必须用具体数字
- label 用中文描述，如"线段AB（有两个端点）"
- 所有图用中文标注，不用符号

用 write_file 存入 {output_file}
格式：{{"test_id":"{test_id}","date":"{today_str}","type":"geometry_preview","questions":[{{id,question,answer,hint,image_path:"draw_geometry生成的图片路径",difficulty:"basic",topic_id}}]}}

⚠️ 只存储，不要调用 send_feishu！（已被系统禁用）