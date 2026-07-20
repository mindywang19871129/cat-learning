请执行每日几何强化出题。

⚠️⚠️⚠️ 严禁调用 send_feishu！系统已被配置为拒绝所有 send_feishu 调用！

test_id = "{test_id}"
每道题用 _gen_question_id() 生成全局唯一编号

1. 读取 data/mastery.json → 找到几何相关（math-2 图形运动 / math-3 周长）
2. 读取 data/error_book.json → 筛选几何错题

出题（3题选择题，用数字标注，不准用符号占位）：
- 轴对称：1题（选择题，配图）
  例：观察图中的对称图形，对称轴在哪里？
  A. 竖直线  B. 水平线  C. 对角线  D. 没有对称轴
- 周长计算：1题（选择题，配图）
  例：如图，一个长方形长8cm、宽5cm，它的周长是多少？
  A. 13cm  B. 26cm  C. 40cm  D. 48cm
- 图形综合：1题（选择题，结合实际场景，配图）
  例：如图，正方形花坛边长6m，绕花坛走一圈是多少米？
  A. 12m  B. 18m  C. 24m  D. 36m

⚠️ 铁则：
- 所有题目必须用选择题格式（A/B/C/D），学生直接回复字母
- 答案格式：{{"answer":"B"}} 或 {{"answer":"(1) A (2) B"}}
- draw_geometry 绘图规范：
  参数必须用具体数字，如 {{"type":"rect","width":8,"height":5,"label":"长8cm宽5cm的长方形"}}
  三角形例：{{"type":"triangle","points":[[0,0],[6,0],[3,5]],"label":"三角形ABC"}}
  正方形例：{{"type":"square","side":6,"label":"边长6cm的正方形"}}
  label 用中文描述，如"长8cm宽5cm的长方形"，不要用△或●符号
- 每道题先用 draw_geometry 绘制图形，然后将图片路径记录下来
- 每题给出完整标准答案

用 write_file 存入 data/questions/questions_{today_str}_geometry.json
格式：{{"test_id":"{test_id}","date":"{today_str}","type":"geometry","questions":[{{id,question,answer,hint,image_path:"draw_geometry生成的图片路径"}}]}}

⚠️ 只存储，不要调用 send_feishu！（已被系统禁用）
