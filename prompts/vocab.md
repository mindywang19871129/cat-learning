请执行每日KET词汇出题。

⚠️ send_feishu已被系统禁用，只存储文件！

test_id = "{test_id}"
每道题用 _gen_question_id() 生成全局唯一编号

1. 读取 data/ket_vocabulary.json（如不存在则用 call_llm 从KET词表创建，至少50词）
2. 筛选：status='new'的选5个新词，status='learning'的选5个复习词

出题（KET风格，英英释义）：
- ⚠️ 必须用英语解释英语，禁止出现中文！
- 新词5题：英英释义匹配（给出英文释义，4个选项）
- 复习词5题：语境填空（英文句子+英文提示）

更新词库：新词→learning、复习词review_count+1、≥3→mastered

用 write_file 存入 data/questions/questions_{today_str}_vocab.json
格式：{{"test_id":"{test_id}","date":"{today_str}","type":"vocab","questions":[{{id,question,answer,hint}}]}}
