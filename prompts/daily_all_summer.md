请一次性生成今日全部暑假学习任务。send_feishu已被系统禁用，只存储文件！

═══════════════════════════════════════
任务1：计算热身 (test_id={test_id_c})
═══════════════════════════════════════
纯计算6题（暑假减量）：两位数×一位数1题 + 三位数÷一位数1题 + 三位数加减法1题 + 四则混合1题 + 连乘/连除1题 + 大数读写1题。每道题用 _gen_question_id() 生成编号，给出标准答案。
用 write_file 存入 data/questions/questions_{today_str}_calc.json
格式：{{"test_id":"{test_id_c}","date":"{today_str}","type":"calc","questions":[{{id,question,answer}}]}}

═══════════════════════════════════════
任务2：数学新概念 (test_id={test_id_m})
═══════════════════════════════════════
读 data/knowledge_map_4th.json 和 data/mastery.json，找下一个未掌握的单元。用生活化语言讲解该单元的一个核心概念（200-300字），像老师讲课一样。包括：生活引入→概念定义→核心要点→例题演示→常见误区→小口诀。0道题，纯讲解。
用 write_file 存入 data/questions/questions_{today_str}_math_preview.json
格式：{{"test_id":"{test_id_m}","date":"{today_str}","type":"math_preview","unit":"单元名","topic":"知识点","introduction":"完整讲解","questions":[]}}

═══════════════════════════════════════
任务3：数学练习 (test_id={test_id_n})
═══════════════════════════════════════
针对任务2讲的知识点，出3道基础题（basic）：直接套用1题+稍加变化1题+生活应用1题。每题给出标准答案+解题步骤。
用 write_file 存入 data/questions/questions_{today_str}_math_practice.json
格式：{{"test_id":"{test_id_n}","date":"{today_str}","type":"math_practice","questions":[{{id,question,answer,hint,difficulty:"basic",topic_id}}]}}

═══════════════════════════════════════
任务4：KET阅读 (test_id={test_id_k})
═══════════════════════════════════════
读 KET备考计划.md 和 data/ket_vocabulary.json。6题按KET真题格式，当天日期%3轮换题型：%3==0→Part1短信息匹配3+Part3长文选择3，%3==1→Part2短文匹配3+Part4完形填空3，%3==2→Part5开放式完形3+Part3长文选择3。词汇严格KET词表，短文50-150词，选项A/B/C。
用 write_file 存入 data/questions/questions_{today_str}_ket_reading.json
格式：{{"test_id":"{test_id_k}","date":"{today_str}","type":"ket_reading","questions":[{{id,question,answer,hint,ket_part}}]}}

═══════════════════════════════════════
任务5：KET写作 (test_id={test_id_w})
═══════════════════════════════════════
读 KET备考计划.md。1题按KET真题，当天日期%2轮换：偶数日→Part6写邮件/便条(≥25词)，奇数日→Part7看图写故事(≥35词)。给出评分标准（内容3分+语言3分+结构3分）和范文。
用 write_file 存入 data/questions/questions_{today_str}_ket_writing.json
格式：Part6: {{"test_id":"{test_id_w}","date":"{today_str}","type":"ket_writing","ket_part":"Part6","prompt":"...","expected_points":["..."],"reference":"..."}}
Part7: {{"test_id":"{test_id_w}","date":"{today_str}","type":"ket_writing","ket_part":"Part7","prompt":"...","picture_descriptions":["..."],"reference":"..."}}

═══════════════════════════════════════
任务6：几何探索 (test_id={test_id_geo})
═══════════════════════════════════════
读 data/knowledge_map_4th.json 的 math-4a-6~10（线与角）和 math-4a-18~20（方向与位置）。2题：线与角1题（用draw_geometry画线段/射线/直线对比图或角分类图）+方向与位置1题（用draw_geometry画网格图标注位置）。每题配图+标准答案。
用 write_file 存入 data/questions/questions_{today_str}_geometry_preview.json
格式：{{"test_id":"{test_id_geo}","date":"{today_str}","type":"geometry_preview","questions":[{{id,question,answer,hint,image_path:"...",difficulty:"basic",topic_id}}]}}

═══════════════════════════════════════
完成后确认：6个文件全部已写入，不要调用send_feishu。
═══════════════════════════════════════