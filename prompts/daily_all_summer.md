请一次性生成今日全部暑假学习任务。send_feishu已被系统禁用，只存储文件！

⚠️⚠️⚠️ 核心流程：用 call_llm 逐任务生成，绝不能一次生成全部6个任务！一次生成会导致输出截断。

⚠️ 核心逻辑：用 data/mastery.json 动态追踪进度。已引入的四上知识点（score=10）可在计算中出题，未引入的（score=0或无记录）只能在数学新概念中讲。

═══════════════════════════════════════
任务1：计算热身 (test_id={test_id_c})
═══════════════════════════════════════
读 data/mastery.json 和 data/error_book.json。
用 call_llm 生成7道纯计算题：
- 三下计算：两位数×一位数、三位数÷一位数、三位数加减法、四则混合、连乘/连除、有余数除法（各1题，共6题）
- 已引入的四上计算：如果 mastery.json 中有 score=10 的四上知识点（如大数读写、三位数×两位数），可出1题替代上面1题
- 三下复习：从 error_book 选1道三下计算错题做变式
如果 error_book 为空，则出7题三下计算。
每道题用 _gen_question_id() 生成编号，给出标准答案。
用 write_file 存入 data/questions/questions_{today_str}_calc.json
格式：{{"test_id":"{test_id_c}","date":"{today_str}","type":"calc","questions":[{{id,question,answer}}]}}

═══════════════════════════════════════
任务2：数学新概念 (test_id={test_id_m})
═══════════════════════════════════════
读 data/knowledge_map_4th.json 和 data/mastery.json。
用 call_llm 生成下一个未掌握单元的讲解（200-300字）。
格式：生活引入→概念定义→核心要点→例题演示→常见误区→小口诀。
0道题，纯讲解。
⚠️ 讲解完成后，必须用 edit_file 更新 data/mastery.json，将对应 topic_id 的 score 设为 10，status 设为 "introduced"。
用 write_file 存入 data/questions/questions_{today_str}_math_preview.json
格式：{{"test_id":"{test_id_m}","date":"{today_str}","type":"math_preview","unit":"单元名","topic":"知识点","topic_id":"math-4a-X","introduction":"完整讲解","questions":[]}}

═══════════════════════════════════════
任务3：数学练习 (test_id={test_id_n})
═══════════════════════════════════════
用 call_llm 针对任务2刚讲的知识点出3道基础题（basic）：
- 直接套用概念1题 + 稍加变化1题 + 生活应用1题
- 如果 error_book 有三下错题，加1道三下应用变式题（共4题）
每题给出标准答案+解题步骤。
用 write_file 存入 data/questions/questions_{today_str}_math_practice.json
格式：{{"test_id":"{test_id_n}","date":"{today_str}","type":"math_practice","questions":[{{id,question,answer,hint,difficulty:"basic",topic_id}}]}}

═══════════════════════════════════════
任务4：KET阅读 (test_id={test_id_k})
═══════════════════════════════════════
⚠️⚠️⚠️ 逐题生成！每道题用 call_llm 单独生成，不能用一次 call_llm 生成全部6题！
读 KET备考计划.md 和 data/ket_vocabulary.json。
当天日期%3轮换题型：%3==0→Part1短信息匹配3+Part3长文选择3，%3==1→Part2短文匹配3+Part4完形填空3，%3==2→Part5开放式完形3+Part3长文选择3。
每道题 call_llm 的 prompt：
"请生成一道KET阅读真题（只生成1道题）：
题型：PartX
词汇范围：KET核心词表~1500词
输出JSON：{{id,question,answer,hint,ket_part}}
⚠️ question必须包含完整阅读原文，禁止截断！"
词汇严格KET词表，短文50-150词，选项A/B/C。
全部6题生成完后，用 write_file 存入 data/questions/questions_{today_str}_ket_reading.json
格式：{{"test_id":"{test_id_k}","date":"{today_str}","type":"ket_reading","questions":[6道题]}}

═══════════════════════════════════════
任务5：KET写作 (test_id={test_id_w})
═══════════════════════════════════════
用 call_llm 生成1题KET写作真题。
当天日期%2轮换：偶数日→Part6写邮件/便条(≥25词)，奇数日→Part7看图写故事(≥35词)。
给出评分标准（内容3分+语言3分+结构3分）和范文。
用 write_file 存入 data/questions/questions_{today_str}_ket_writing.json
格式：Part6: {{"test_id":"{test_id_w}","date":"{today_str}","type":"ket_writing","ket_part":"Part6","prompt":"...","expected_points":["..."],"reference":"..."}}
Part7: {{"test_id":"{test_id_w}","date":"{today_str}","type":"ket_writing","ket_part":"Part7","prompt":"...","picture_descriptions":["..."],"reference":"..."}}

═══════════════════════════════════════
任务6：几何探索 (test_id={test_id_geo})
═══════════════════════════════════════
用 call_llm 生成2题几何题。
读 data/knowledge_map_4th.json 的 math-4a-6~10（线与角）和 math-4a-18~20（方向与位置）。
2题：线与角1题（用draw_geometry画线段/射线/直线对比图或角分类图）+方向与位置1题（用draw_geometry画网格图标注位置）。
每题配图+标准答案。
用 write_file 存入 data/questions/questions_{today_str}_geometry_preview.json
格式：{{"test_id":"{test_id_geo}","date":"{today_str}","type":"geometry_preview","questions":[{{id,question,answer,hint,image_path:"...",difficulty:"basic",topic_id}}]}}

═══════════════════════════════════════
完成后确认：6个文件全部已写入，mastery.json已更新，不要调用send_feishu。
═══════════════════════════════════════