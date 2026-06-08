"""
KET单词训练营 - 后端API
启动: python3 ket_vocab_server.py
端口: 8193
"""
import json, random, os
from datetime import datetime, date
from pathlib import Path
from flask import Flask, jsonify, request, send_file

app = Flask(__name__)
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
VOCAB_FILE = DATA_DIR / "ket_vocabulary.json"
PROGRESS_FILE = DATA_DIR / "ket_vocab_progress.json"

# ==================== KET词库（~180词，按主题） ====================
DEFAULT_VOCAB = [
    # 家庭
    {"w":"family","c":"家庭","p":"n.","e":"I love my family.","t":"家庭"},
    {"w":"mother","c":"母亲","p":"n.","e":"My mother is a teacher.","t":"家庭"},
    {"w":"father","c":"父亲","p":"n.","e":"His father works in a hospital.","t":"家庭"},
    {"w":"sister","c":"姐妹","p":"n.","e":"She has an older sister.","t":"家庭"},
    {"w":"brother","c":"兄弟","p":"n.","e":"My brother is ten years old.","t":"家庭"},
    {"w":"baby","c":"婴儿","p":"n.","e":"The baby is sleeping.","t":"家庭"},
    {"w":"friend","c":"朋友","p":"n.","e":"Tom is my best friend.","t":"家庭"},
    {"w":"name","c":"名字","p":"n.","e":"What is your name?","t":"家庭"},
    {"w":"child","c":"孩子","p":"n.","e":"The child is playing.","t":"家庭"},
    {"w":"parent","c":"父母","p":"n.","e":"My parents are kind.","t":"家庭"},
    # 学校
    {"w":"school","c":"学校","p":"n.","e":"I go to school every day.","t":"学校"},
    {"w":"teacher","c":"老师","p":"n.","e":"Our teacher is nice.","t":"学校"},
    {"w":"student","c":"学生","p":"n.","e":"She is a good student.","t":"学校"},
    {"w":"class","c":"班级","p":"n.","e":"My class has 30 students.","t":"学校"},
    {"w":"book","c":"书","p":"n.","e":"I read a book every night.","t":"学校"},
    {"w":"pen","c":"笔","p":"n.","e":"Can I borrow your pen?","t":"学校"},
    {"w":"pencil","c":"铅笔","p":"n.","e":"I need a pencil.","t":"学校"},
    {"w":"homework","c":"作业","p":"n.","e":"I finish my homework at 7.","t":"学校"},
    {"w":"learn","c":"学习","p":"v.","e":"We learn English at school.","t":"学校"},
    {"w":"write","c":"写","p":"v.","e":"Please write your name.","t":"学校"},
    {"w":"read","c":"读","p":"v.","e":"Can you read this book?","t":"学校"},
    {"w":"study","c":"学习","p":"v.","e":"I study hard every day.","t":"学校"},
    # 食物
    {"w":"food","c":"食物","p":"n.","e":"I like Chinese food.","t":"食物"},
    {"w":"apple","c":"苹果","p":"n.","e":"I eat an apple every day.","t":"食物"},
    {"w":"bread","c":"面包","p":"n.","e":"I have bread for breakfast.","t":"食物"},
    {"w":"rice","c":"米饭","p":"n.","e":"We eat rice for lunch.","t":"食物"},
    {"w":"milk","c":"牛奶","p":"n.","e":"I drink milk every morning.","t":"食物"},
    {"w":"water","c":"水","p":"n.","e":"Please give me some water.","t":"食物"},
    {"w":"egg","c":"鸡蛋","p":"n.","e":"I have an egg for breakfast.","t":"食物"},
    {"w":"cake","c":"蛋糕","p":"n.","e":"The cake is delicious.","t":"食物"},
    {"w":"chicken","c":"鸡肉","p":"n.","e":"We have chicken for dinner.","t":"食物"},
    {"w":"fruit","c":"水果","p":"n.","e":"I like eating fruit.","t":"食物"},
    {"w":"vegetable","c":"蔬菜","p":"n.","e":"Eat more vegetables.","t":"食物"},
    {"w":"breakfast","c":"早餐","p":"n.","e":"Breakfast is important.","t":"食物"},
    {"w":"lunch","c":"午餐","p":"n.","e":"What's for lunch?","t":"食物"},
    {"w":"dinner","c":"晚餐","p":"n.","e":"Dinner is at 6 o'clock.","t":"食物"},
    # 运动
    {"w":"sport","c":"运动","p":"n.","e":"I like playing sports.","t":"运动"},
    {"w":"run","c":"跑步","p":"v.","e":"I run every morning.","t":"运动"},
    {"w":"swim","c":"游泳","p":"v.","e":"Can you swim?","t":"运动"},
    {"w":"play","c":"玩","p":"v.","e":"Let's play football.","t":"运动"},
    {"w":"football","c":"足球","p":"n.","e":"I play football after school.","t":"运动"},
    {"w":"basketball","c":"篮球","p":"n.","e":"He likes basketball.","t":"运动"},
    {"w":"tennis","c":"网球","p":"n.","e":"She plays tennis well.","t":"运动"},
    {"w":"walk","c":"走路","p":"v.","e":"I walk to school.","t":"运动"},
    {"w":"ride","c":"骑","p":"v.","e":"I ride my bike to school.","t":"运动"},
    {"w":"bike","c":"自行车","p":"n.","e":"My bike is blue.","t":"运动"},
    # 天气
    {"w":"weather","c":"天气","p":"n.","e":"The weather is nice today.","t":"天气"},
    {"w":"sun","c":"太阳","p":"n.","e":"The sun is shining.","t":"天气"},
    {"w":"rain","c":"雨","p":"n.","e":"It is raining outside.","t":"天气"},
    {"w":"snow","c":"雪","p":"n.","e":"It snows in winter.","t":"天气"},
    {"w":"wind","c":"风","p":"n.","e":"The wind is strong.","t":"天气"},
    {"w":"cloud","c":"云","p":"n.","e":"There are many clouds.","t":"天气"},
    {"w":"hot","c":"热的","p":"adj.","e":"It is very hot today.","t":"天气"},
    {"w":"cold","c":"冷的","p":"adj.","e":"It is cold in winter.","t":"天气"},
    {"w":"warm","c":"温暖的","p":"adj.","e":"Spring is warm.","t":"天气"},
    {"w":"cool","c":"凉爽的","p":"adj.","e":"Autumn is cool.","t":"天气"},
    # 动物
    {"w":"animal","c":"动物","p":"n.","e":"I love animals.","t":"动物"},
    {"w":"cat","c":"猫","p":"n.","e":"The cat is sleeping.","t":"动物"},
    {"w":"dog","c":"狗","p":"n.","e":"I have a dog.","t":"动物"},
    {"w":"bird","c":"鸟","p":"n.","e":"The bird can fly.","t":"动物"},
    {"w":"fish","c":"鱼","p":"n.","e":"There are fish in the water.","t":"动物"},
    {"w":"horse","c":"马","p":"n.","e":"The horse runs fast.","t":"动物"},
    {"w":"rabbit","c":"兔子","p":"n.","e":"The rabbit is white.","t":"动物"},
    {"w":"elephant","c":"大象","p":"n.","e":"The elephant is big.","t":"动物"},
    # 颜色
    {"w":"colour","c":"颜色","p":"n.","e":"What colour do you like?","t":"颜色"},
    {"w":"red","c":"红色","p":"adj.","e":"The apple is red.","t":"颜色"},
    {"w":"blue","c":"蓝色","p":"adj.","e":"The sky is blue.","t":"颜色"},
    {"w":"green","c":"绿色","p":"adj.","e":"The grass is green.","t":"颜色"},
    {"w":"yellow","c":"黄色","p":"adj.","e":"The banana is yellow.","t":"颜色"},
    {"w":"white","c":"白色","p":"adj.","e":"Snow is white.","t":"颜色"},
    {"w":"black","c":"黑色","p":"adj.","e":"The cat is black.","t":"颜色"},
    {"w":"pink","c":"粉色","p":"adj.","e":"She likes pink.","t":"颜色"},
    # 数字时间
    {"w":"time","c":"时间","p":"n.","e":"What time is it?","t":"数字"},
    {"w":"day","c":"天","p":"n.","e":"Have a nice day!","t":"数字"},
    {"w":"week","c":"周","p":"n.","e":"I go swimming every week.","t":"数字"},
    {"w":"month","c":"月","p":"n.","e":"My birthday is this month.","t":"数字"},
    {"w":"year","c":"年","p":"n.","e":"I am ten years old.","t":"数字"},
    {"w":"today","c":"今天","p":"n.","e":"Today is Monday.","t":"数字"},
    {"w":"tomorrow","c":"明天","p":"n.","e":"See you tomorrow.","t":"数字"},
    {"w":"yesterday","c":"昨天","p":"n.","e":"I was busy yesterday.","t":"数字"},
    {"w":"morning","c":"早上","p":"n.","e":"Good morning!","t":"数字"},
    {"w":"afternoon","c":"下午","p":"n.","e":"Good afternoon!","t":"数字"},
    {"w":"evening","c":"晚上","p":"n.","e":"Good evening!","t":"数字"},
    {"w":"night","c":"夜晚","p":"n.","e":"Good night!","t":"数字"},
    {"w":"clock","c":"钟","p":"n.","e":"Look at the clock.","t":"数字"},
    {"w":"hour","c":"小时","p":"n.","e":"I study for one hour.","t":"数字"},
    {"w":"minute","c":"分钟","p":"n.","e":"Wait a minute.","t":"数字"},
    # 衣物
    {"w":"clothes","c":"衣服","p":"n.","e":"I need new clothes.","t":"衣物"},
    {"w":"shirt","c":"衬衫","p":"n.","e":"He wears a white shirt.","t":"衣物"},
    {"w":"dress","c":"连衣裙","p":"n.","e":"She has a beautiful dress.","t":"衣物"},
    {"w":"shoe","c":"鞋","p":"n.","e":"I need new shoes.","t":"衣物"},
    {"w":"hat","c":"帽子","p":"n.","e":"Put on your hat.","t":"衣物"},
    {"w":"coat","c":"外套","p":"n.","e":"Wear a coat, it's cold.","t":"衣物"},
    # 旅行
    {"w":"travel","c":"旅行","p":"v.","e":"I like to travel.","t":"旅行"},
    {"w":"train","c":"火车","p":"n.","e":"We go by train.","t":"旅行"},
    {"w":"bus","c":"公交车","p":"n.","e":"I take the bus to school.","t":"旅行"},
    {"w":"car","c":"汽车","p":"n.","e":"My father has a car.","t":"旅行"},
    {"w":"plane","c":"飞机","p":"n.","e":"We go to Beijing by plane.","t":"旅行"},
    {"w":"ticket","c":"票","p":"n.","e":"I need a ticket.","t":"旅行"},
    {"w":"map","c":"地图","p":"n.","e":"Can I have a map?","t":"旅行"},
    {"w":"hotel","c":"酒店","p":"n.","e":"We stay at a hotel.","t":"旅行"},
    # 动词
    {"w":"be","c":"是","p":"v.","e":"I am a student.","t":"动词"},
    {"w":"have","c":"有","p":"v.","e":"I have a book.","t":"动词"},
    {"w":"do","c":"做","p":"v.","e":"What do you do?","t":"动词"},
    {"w":"go","c":"去","p":"v.","e":"I go to school.","t":"动词"},
    {"w":"come","c":"来","p":"v.","e":"Come here, please.","t":"动词"},
    {"w":"see","c":"看见","p":"v.","e":"I can see a bird.","t":"动词"},
    {"w":"like","c":"喜欢","p":"v.","e":"I like ice cream.","t":"动词"},
    {"w":"want","c":"想要","p":"v.","e":"I want a new book.","t":"动词"},
    {"w":"know","c":"知道","p":"v.","e":"I know the answer.","t":"动词"},
    {"w":"think","c":"想","p":"v.","e":"I think so.","t":"动词"},
    {"w":"say","c":"说","p":"v.","e":"What did you say?","t":"动词"},
    {"w":"tell","c":"告诉","p":"v.","e":"Tell me a story.","t":"动词"},
    {"w":"give","c":"给","p":"v.","e":"Give me the book.","t":"动词"},
    {"w":"take","c":"拿","p":"v.","e":"Take your bag.","t":"动词"},
    {"w":"make","c":"制作","p":"v.","e":"I can make a cake.","t":"动词"},
    {"w":"eat","c":"吃","p":"v.","e":"I eat breakfast at 7.","t":"动词"},
    {"w":"drink","c":"喝","p":"v.","e":"I drink water.","t":"动词"},
    {"w":"sleep","c":"睡觉","p":"v.","e":"I sleep at 9 o'clock.","t":"动词"},
    {"w":"live","c":"居住","p":"v.","e":"I live in Beijing.","t":"动词"},
    {"w":"work","c":"工作","p":"v.","e":"My father works hard.","t":"动词"},
    {"w":"buy","c":"买","p":"v.","e":"I want to buy a book.","t":"动词"},
    {"w":"open","c":"打开","p":"v.","e":"Open the door, please.","t":"动词"},
    {"w":"close","c":"关闭","p":"v.","e":"Close the window.","t":"动词"},
    {"w":"start","c":"开始","p":"v.","e":"Let's start the class.","t":"动词"},
    {"w":"stop","c":"停止","p":"v.","e":"Please stop talking.","t":"动词"},
    {"w":"help","c":"帮助","p":"v.","e":"Can you help me?","t":"动词"},
    {"w":"need","c":"需要","p":"v.","e":"I need your help.","t":"动词"},
    # 形容词
    {"w":"big","c":"大的","p":"adj.","e":"The house is big.","t":"形容词"},
    {"w":"small","c":"小的","p":"adj.","e":"The cat is small.","t":"形容词"},
    {"w":"good","c":"好的","p":"adj.","e":"This is a good book.","t":"形容词"},
    {"w":"bad","c":"坏的","p":"adj.","e":"That's a bad idea.","t":"形容词"},
    {"w":"new","c":"新的","p":"adj.","e":"I have a new bag.","t":"形容词"},
    {"w":"old","c":"旧的","p":"adj.","e":"This is an old house.","t":"形容词"},
    {"w":"happy","c":"开心的","p":"adj.","e":"I am very happy.","t":"形容词"},
    {"w":"sad","c":"难过的","p":"adj.","e":"Don't be sad.","t":"形容词"},
    {"w":"beautiful","c":"美丽的","p":"adj.","e":"The flower is beautiful.","t":"形容词"},
    {"w":"nice","c":"好的","p":"adj.","e":"Have a nice day!","t":"形容词"},
    {"w":"easy","c":"容易的","p":"adj.","e":"This test is easy.","t":"形容词"},
    {"w":"difficult","c":"困难的","p":"adj.","e":"Math is difficult.","t":"形容词"},
    {"w":"important","c":"重要的","p":"adj.","e":"Breakfast is important.","t":"形容词"},
    {"w":"different","c":"不同的","p":"adj.","e":"They are different.","t":"形容词"},
    {"w":"right","c":"正确的","p":"adj.","e":"That's right!","t":"形容词"},
    {"w":"wrong","c":"错误的","p":"adj.","e":"Sorry, that's wrong.","t":"形容词"},
    {"w":"long","c":"长的","p":"adj.","e":"The river is long.","t":"形容词"},
    {"w":"short","c":"短的","p":"adj.","e":"The pencil is short.","t":"形容词"},
    {"w":"young","c":"年轻的","p":"adj.","e":"She is young.","t":"形容词"},
    {"w":"tall","c":"高的","p":"adj.","e":"He is very tall.","t":"形容词"},
    # 地点
    {"w":"home","c":"家","p":"n.","e":"I go home at 5.","t":"地点"},
    {"w":"house","c":"房子","p":"n.","e":"My house is big.","t":"地点"},
    {"w":"room","c":"房间","p":"n.","e":"This is my room.","t":"地点"},
    {"w":"kitchen","c":"厨房","p":"n.","e":"Mum is in the kitchen.","t":"地点"},
    {"w":"garden","c":"花园","p":"n.","e":"We have a garden.","t":"地点"},
    {"w":"park","c":"公园","p":"n.","e":"Let's go to the park.","t":"地点"},
    {"w":"shop","c":"商店","p":"n.","e":"I go to the shop.","t":"地点"},
    {"w":"library","c":"图书馆","p":"n.","e":"I study in the library.","t":"地点"},
    {"w":"hospital","c":"医院","p":"n.","e":"He is in the hospital.","t":"地点"},
    {"w":"cinema","c":"电影院","p":"n.","e":"Let's go to the cinema.","t":"地点"},
    {"w":"street","c":"街道","p":"n.","e":"The street is busy.","t":"地点"},
    {"w":"city","c":"城市","p":"n.","e":"Beijing is a big city.","t":"地点"},
    {"w":"country","c":"国家","p":"n.","e":"China is a big country.","t":"地点"},
    # 节日
    {"w":"birthday","c":"生日","p":"n.","e":"Happy birthday!","t":"节日"},
    {"w":"party","c":"聚会","p":"n.","e":"Let's have a party!","t":"节日"},
    {"w":"present","c":"礼物","p":"n.","e":"Here is your present.","t":"节日"},
    {"w":"holiday","c":"假期","p":"n.","e":"I like summer holiday.","t":"节日"},
    # 身体
    {"w":"body","c":"身体","p":"n.","e":"Exercise is good for your body.","t":"身体"},
    {"w":"head","c":"头","p":"n.","e":"I have a headache.","t":"身体"},
    {"w":"hand","c":"手","p":"n.","e":"Wash your hands.","t":"身体"},
    {"w":"eye","c":"眼睛","p":"n.","e":"She has blue eyes.","t":"身体"},
    {"w":"mouth","c":"嘴巴","p":"n.","e":"Open your mouth.","t":"身体"},
    {"w":"foot","c":"脚","p":"n.","e":"My foot hurts.","t":"身体"},
]

# ==================== 数据管理 ====================
def load_vocab():
    if VOCAB_FILE.exists():
        return json.loads(VOCAB_FILE.read_text(encoding="utf-8"))
    data = [dict(w, status="new", learned=None, review_count=0, last_review=None) for w in DEFAULT_VOCAB]
    VOCAB_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data

def save_vocab(data):
    VOCAB_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def load_progress():
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    return {"test_done": False, "test_date": None, "test_score": 0, "daily_streak": 0, "last_daily_date": None, "daily_history": []}

def save_progress(p):
    PROGRESS_FILE.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")

# ==================== API ====================
@app.route("/api/stats")
def api_stats():
    vocab = load_vocab()
    progress = load_progress()
    mastered = sum(1 for w in vocab if w["status"] == "mastered")
    learning = sum(1 for w in vocab if w["status"] == "learning")
    new_count = sum(1 for w in vocab if w["status"] == "new")
    pct = round(mastered / len(vocab) * 100) if vocab else 0
    today_str = date.today().isoformat()
    today_done = sum(1 for h in progress.get("daily_history", []) if h.get("date") == today_str)
    return jsonify({
        "total": len(vocab), "mastered": mastered, "learning": learning, "new": new_count,
        "pct": pct, "test_done": progress["test_done"], "test_date": progress.get("test_date"),
        "test_score": progress.get("test_score", 0), "daily_streak": progress.get("daily_streak", 0),
        "today_done": today_done
    })

@app.route("/api/test/start")
def api_test_start():
    vocab = load_vocab()
    pool = [{"w": w["w"], "c": w["c"], "p": w["p"], "e": w["e"]} for w in vocab]
    random.shuffle(pool)
    return jsonify({"words": pool[:30]})

@app.route("/api/test/submit", methods=["POST"])
def api_test_submit():
    data = request.get_json()
    results = data.get("results", [])  # [{w:"apple", answer:"yes"}, ...]
    vocab = load_vocab()
    progress = load_progress()
    known = sum(1 for r in results if r["answer"] == "yes")
    score = round(known / len(results) * 100) if results else 0
    # 更新词库状态
    for r in results:
        for w in vocab:
            if w["w"] == r["w"] and r["answer"] != "yes":
                w["status"] = "learning"
                w["learned"] = date.today().isoformat()
    save_vocab(vocab)
    progress["test_done"] = True
    progress["test_date"] = date.today().isoformat()
    progress["test_score"] = score
    save_progress(progress)
    return jsonify({"score": score, "known": known, "total": len(results), "vocab_estimate": round(known / 30 * len(vocab))})

@app.route("/api/daily/words")
def api_daily_words():
    vocab = load_vocab()
    learning = [w for w in vocab if w["status"] in ("learning", "new")]
    pool = learning if learning else vocab
    random.shuffle(pool)
    return jsonify({"words": [{"w": w["w"], "c": w["c"], "p": w["p"], "e": w["e"]} for w in pool[:10]]})

@app.route("/api/daily/submit", methods=["POST"])
def api_daily_submit():
    data = request.get_json()
    word = data.get("word", "")
    mode = data.get("mode", "")
    correct = data.get("correct", False)
    vocab = load_vocab()
    progress = load_progress()
    today_str = date.today().isoformat()
    # 更新词状态
    for w in vocab:
        if w["w"] == word:
            w["review_count"] = w.get("review_count", 0) + 1
            w["last_review"] = today_str
            if correct and w["review_count"] >= 3:
                w["status"] = "mastered"
            elif correct:
                w["status"] = "learning"
            break
    save_vocab(vocab)
    # 更新打卡
    if progress.get("last_daily_date") != today_str:
        progress["daily_streak"] = progress.get("daily_streak", 0) + 1
        progress["last_daily_date"] = today_str
    progress.setdefault("daily_history", []).append({"date": today_str, "word": word, "mode": mode, "correct": correct})
    save_progress(progress)
    return jsonify({"ok": True})

@app.route("/api/review/words")
def api_review_words():
    vocab = load_vocab()
    learning = [{"w": w["w"], "c": w["c"], "p": w["p"], "e": w["e"], "status": w["status"], "review_count": w.get("review_count", 0)} for w in vocab if w["status"] in ("learning", "new")]
    return jsonify({"words": learning})

@app.route("/api/export/unknown")
def api_export_unknown():
    vocab = load_vocab()
    unknown = [w for w in vocab if w["status"] != "mastered"]
    lines = [f"{w['w']} | {w['c']} | {w['p']} | {w['e']} | 状态:{w['status']}" for w in unknown]
    return jsonify({"count": len(unknown), "words": unknown, "text": "\n".join(lines)})

@app.route("/")
def index():
    return send_file("ket_vocab_trainer.html")

if __name__ == "__main__":
    print("🐱 KET单词训练营后端启动: http://0.0.0.0:8193")
    app.run(host="0.0.0.0", port=8193, debug=True)
