请执行每日几何强化出题。

⚠️⚠️⚠️ 严禁调用 send_feishu！系统已被配置为拒绝所有 send_feishu 调用！

test_id = "{test_id}"
每道题用 _gen_question_id() 生成全局唯一编号

1. 读取 data/mastery.json → 找到几何相关（math-2 图形运动 / math-3 周长）
2. 读取 data/error_book.json → 筛选几何错题

出题（3题，用数字标注，不准用符号占位）：
- 轴对称：1题（如"画出一个对称轴，写出对称点到轴的距离"）
- 周长计算：1题（如"一个长方形长8cm、宽5cm，求周长"）
- 图形综合：1题（结合实际场景）
- ⚠️ draw_geometry 绘图规范：
  参数必须用具体数字，如 {{"type":"rect","width":8,"height":5,"label":"长8cm宽5cm的长方形"}}
  三角形例：{{"type":"triangle","points":[[0,0],[6,0],[3,5]],"label":"三角形ABC"}}
  正方形例：{{"type":"square","side":6,"label":"边长6cm的正方形"}}
  label 用中文描述，如"长8cm宽5cm的长方形"，不要用△或●符号
- 每道题先用 draw_geometry 绘制图形，然后将图片路径记录下来
- 每题给出完整标准答案

用 write_file 存入 data/questions/questions_{today_str}_geometry.json
格式：{{"test_id":"{test_id}","date":"{today_str}","type":"geometry","questions":[{{id,question,answer,hint,image_path:"draw_geometry生成的图片路径"}}]}}

⚠️ 只存储，不要调用 send_feishu！（已被系统禁用）
