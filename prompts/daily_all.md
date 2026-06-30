请一次性生成今日全部学习任务。send_feishu已被系统禁用，只存储文件！

═══════════════════════════════════════
任务1：计算热身 (test_id={test_id_c})
═══════════════════════════════════════
纯计算10题：两位数×一位数2题 + 三位数÷一位数2题 + 三位数加减法2题 + 四则混合2题 + 连乘/连除2题。每道题用 _gen_question_id() 生成编号，给出标准答案。
用 write_file 存入 data/questions/questions_{today_str}_calc.json
格式：{{"test_id":"{test_id_c}","date":"{today_str}","type":"calc","questions":[{{id,question,answer}}]}}

═══════════════════════════════════════
任务2：KET词汇 (test_id={test_id_v})
═══════════════════════════════════════
读 data/ket_vocabulary.json，选5新词+5复习词。新词出英英释义匹配（英文释义+4选项），复习词出语境填空（英文句子+英文提示）。必须全英文。更新词库。
用 write_file 存入 data/questions/questions_{today_str}_vocab.json
格式：{{"test_id":"{test_id_v}","date":"{today_str}","type":"vocab","questions":[{{id,question,answer,hint}}]}}

═══════════════════════════════════════
任务3：英语语法 (test_id={test_id_g})
═══════════════════════════════════════
读 KET备考计划.md 和 root.md 格式模板。4题：时态填空1 + 改错1 + 句型转换1 + 完形填空1。填空/改错必须含完整原文和选项。禁止△●符号。
用 write_file 存入 data/questions/questions_{today_str}_grammar.json
格式：{{"test_id":"{test_id_g}","date":"{today_str}","type":"grammar","questions":[{{id,question,answer,hint,topic_id}}]}}

═══════════════════════════════════════
任务4：英语作文 (test_id={test_id_p})
═══════════════════════════════════════
读 KET备考计划.md。1篇≥35词短文，轮换类型（邮件/描述/故事/便条）。给评分要点+范文。
用 write_file 存入 data/questions/questions_{today_str}_writing.json
格式：{{"test_id":"{test_id_p}","date":"{today_str}","type":"writing","prompt":"...","reference":"...","scoring_points":["..."]}}

═══════════════════════════════════════
任务5：数学综合 (test_id={test_id_m})
═══════════════════════════════════════
⚠️ 只出数学题！读 mastery/error_book/adjustments/knowledge_map。4题7单元均衡：乘法1+除法1+周长1+图形运动/数据/实践活动轮换1。禁用水果名，用具体数字不用△●。每题2步推理+标准答案。
用 write_file 存入 data/today_questions.json 和 data/questions/questions_{today_str}.json
格式：{{"test_id":"{test_id_m}","date":"{today_str}","math":[{{id,question,answer,hint,difficulty,topic_id}}],"english":[]}}

═══════════════════════════════════════
任务6：几何强化 (test_id={test_id_geo})
═══════════════════════════════════════
读 mastery 找 math-2/math-3 薄弱点。3题：轴对称1+周长1+图形综合1。每题先用 draw_geometry 绘图（参数用具体数字如{{"type":"rect","width":8,"height":5,"label":"长8cm宽5cm"}}），记录图片路径。label用中文不用△●。
用 write_file 存入 data/questions/questions_{today_str}_geometry.json
格式：{{"test_id":"{test_id_geo}","date":"{today_str}","type":"geometry","questions":[{{id,question,answer,hint,image_path:"..."}}]}}

═══════════════════════════════════════
完成后确认：6个文件全部已写入，不要调用send_feishu。
═══════════════════════════════════════
