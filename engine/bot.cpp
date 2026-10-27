#include<iostream>
#include <vector>
#include <string>
#include <cstdlib>
#include <utility>
#include <random>
#include <chrono>
#include "jsoncpp/json.h" // C++编译时默认包含此库
using namespace std;

#define WALL -2 //处理为墙，与对手棋子一般作为阻挡
const int Size=15;
//提供随机开局，从开局池中选取
mt19937 rng(chrono::steady_clock::now().time_since_epoch().count());

struct ChessType{
    int score;
    vector<int> shape; //0=空，-1=对方棋子，1=己方棋子

    bool operator==(const ChessType& other)const{
        return this->shape==other.shape;
    }

    bool operator==(const vector<int>& shape)const{
        return this->shape==shape;
    }
};

enum class chess{
    EMPTY=0,  //空 0
    BLACK=1,  //（己方）1
    WHITE=-1   //（对手）-1
};

chess Grid[Size][Size];

//也许可优化：怎么样的棋形估值可让胜率达到最高？是否可以通过区分对方己方棋形估值让胜率提高？
const vector<ChessType> Score_List={
    {10,    {0, 0, 1, 1, 0, 0}},    //活二
    {50,    {0, 1, 1, 0, 0}},       //眠二   
    {100,   {0, 1, 0, 1, 0}},       //跳活二
    {100,   {0, 1, 1, 0}},          //活二

    {500,   {0, 0, 1, 1, 1, 0}},    //眠三
    {1000,   {0, 1, 1, 1, 0}},      //活三
    {1000,  {0, 1, 0, 1, 1, 0}},    //跳活三

    {10000,  {1, 1, 1, 1, 0}},      //冲四
    {10000,  {0, 1, 1, 1, 1}},      //冲四
    {50000,  {0, 1, 1, 1, 1, 0}},   //活四

    {10000000,{1, 1, 1, 1, 1}},     //连五
};
const int DOUBLE_LIVE_THREE=8000; //双活三 8000
const int FOUR_THREE=9000; //四三（冲四+活三）9000
const int DOUBLE_FOUR=100000; //双冲四 100000

//开局池，选取有优势，但不在星位内的位置，己方为黑棋先手可在该池中随机选取位置开局
const vector<pair<int,int>> opening_POND={
    {1,1},{1,2},{2,1},{2,2},        //左上
    {1,12},{1,13},{2,12},{2,13},    //右上
    {12,1},{12,2},{13,1},{13,2},    //左下
    {12,12},{12,13},{13,12},{13,13} //右下
};

//方向数组：右、下、右下、左下
int dx[4]={1,0,1,1};
int dy[4]={0,1,1,-1};

//将棋子转化为int
int Convert(chess cell,chess target){
    if(cell==chess::EMPTY)return 0;
    if(cell==target)return 1;
    return -1;
}

//判断是否在棋盘中
bool inBoard(int x,int y){
    return x>=0&&y>=0&&x<Size&&y<Size;
}
//黑棋首子落在星位内必胜，必须换
bool inStarRange(int x,int y){
    return x>=3&&x<=11&&y>=3&&y<=11;
}

//探索整行，返回该行棋形，在（x，y）为中点向dir方向去找，半径为radiux (此处可修正棋色)
vector<int> searchInline(int x,int y,int dir,chess myChess,int radiux=5){
    vector<int> line;
    for(int i=-radiux+1;i<radiux;i++){
        int nx=x+dx[dir]*i;
        int ny=y+dy[dir]*i;

        if(inBoard(nx,ny)){
            line.push_back(Convert(Grid[nx][ny],myChess));
        }
        else{
            line.push_back(WALL);
        }
    }

    return line;
}

//统计对某种棋形的匹配，匹配成功返回棋形得分。（这部分应该可以用kmp优化）
int MatchChessType_calScore(const vector<int>& chessLine,const ChessType& type){
    bool match=false;
    int line_Len=chessLine.size();
    int shape_Len=type.shape.size();

    for(int i=0;i<=line_Len-shape_Len;i++){
        if(match==true)break;
        for(int j=0;j<shape_Len;j++){
            if(chessLine[j+i]!=type.shape[j])break;
            else {
                if(j==shape_Len-1){
                    match=true;
                    break;
                }
            }
        }
    }

    return match?type.score:0;
}

pair<int,int> getRandomOpening(){
    uniform_int_distribution<int> dist(0,opening_POND.size()-1);
    return opening_POND[dist(rng)];
}

//判断在(x,y)落子的得分，myChess提供棋子颜色
int Evaluation_xy_Score(int x,int y,chess myChess,bool isNotFirst=true){
    if(Grid[x][y]!=chess::EMPTY&&isNotFirst)return 0;
    Grid[x][y]=myChess;

    int cnt_LiveThree=0;
    int cnt_rushFour=0;

    int total_score=0;
    for(int i=0;i<4;i++){
        int dir_score=0;
        vector<int> line=searchInline(x,y,i,myChess,6);
        for(auto& chess_type:Score_List){
            dir_score+=MatchChessType_calScore(line,chess_type);
        }
        if(dir_score>=10000)cnt_rushFour++;
        else if(dir_score>=1000)cnt_LiveThree++;
        total_score+=dir_score;
    }

    if(cnt_rushFour>=2)total_score=max(total_score,DOUBLE_FOUR);
    if(cnt_rushFour>=1&&cnt_LiveThree>=1)total_score=max(total_score,FOUR_THREE);
    if(cnt_LiveThree>=2)total_score=max(total_score,DOUBLE_LIVE_THREE);

    if(isNotFirst)Grid[x][y]=chess::EMPTY;
    return total_score;
}

//探索可以下棋的位置，尽量在已有的棋附近，具有更高价值
vector<pair<int,int>> exploreNewPoint(){
    vector<pair<int,int>> newPointCanExp;
    bool visited[Size][Size]={false};

    for(int i=0;i<Size;i++){
        for(int j=0;j<Size;j++){
            if(Grid[i][j]!=chess::EMPTY){
                for(int dir=0;dir<4;dir++){
                    for(int length=-2;length<3;length++){
                        int nx=i+dx[dir]*length;
                        int ny=j+dy[dir]*length;
                        if(inBoard(nx,ny)&&Grid[nx][ny]==chess::EMPTY&&!visited[nx][ny]){
                            visited[nx][ny]=true;
                            newPointCanExp.push_back({nx,ny});
                        }
                    }
                }
            }
        }
    }

    if(newPointCanExp.size()==0){
        auto opening=getRandomOpening();
        newPointCanExp.push_back(opening);
    }

    return newPointCanExp;
}

//通过估值选估值最高点落子，  可优化点：总估值函数，落子点对敌对己估值按什么权重可以让胜率最高？
Json::Value getBestMovement(chess myChess){
    auto candidates=exploreNewPoint();
    chess enemy_chess=(myChess==chess::BLACK)?(chess::WHITE):(chess::BLACK);

    double best_score=-1;
    pair<int,int> best_move={-1,-1};

    for(auto &[x,y]:candidates){
        double my_score=Evaluation_xy_Score(x,y,myChess);
        double enemy_score=Evaluation_xy_Score(x,y,enemy_chess);
        if(my_score>=10000000){
            best_move={x,y};
            break;
        }
        if(enemy_score>=10000000){
            best_move={x,y};
            best_score=99999999;
        }

        double total=my_score+enemy_score*1.2;//总估值函数
        if(best_score<total){
            best_score=total;
            best_move={x,y};
        }
    }

    Json::Value action;
	action["x"] = best_move.first;
	action["y"] = best_move.second;
	return action;
}

//判断黑棋落子位置价值是否值得我们去换手
bool evaluationOfSwap(int fx,int fy){
    if(inStarRange(fx,fy))return true;

    int blackScore = Evaluation_xy_Score(fx, fy, chess::BLACK, false);
    return blackScore>666;//这个阈值可调，优化点：选择最佳阈值，达到最佳胜率
}

void placeAt(int x, int y,chess chess_color)
{
	if (x >= 0 && y >= 0 && inBoard(x,y)) {
		Grid[x][y] = chess_color;
	}
}

int main(){
	//读入JSON
	string str;
	getline(cin, str);
	Json::Reader reader;
	Json::Value input;
	reader.parse(str, input);

    chess myColor=chess::WHITE;
    chess opColor=chess::BLACK;

	// 分析自己收到的输入和自己过往的输出，并恢复状态
	int turnID = input["responses"].size();
	for (int i = 0; i < turnID; i++) {
        int opX=input["requests"][i]["x"].asInt();
        int opY=input["requests"][i]["y"].asInt();
        int myX=input["responses"][i]["x"].asInt();
        int myY=input["responses"][i]["y"].asInt();
		placeAt(opX, opY, opColor);
        if((opX==-1&&opY==-1)||(myX==-1&&myY==-1)){
            swap(opColor,myColor);
        }
		placeAt(myX, myY, myColor);
	}
    
    int opX=input["requests"][turnID]["x"].asInt();
    int opY=input["requests"][turnID]["y"].asInt();
    if(opX==-1&&opY==-1){
        swap(opColor,myColor);
    }
	placeAt(opX, opY, opColor);

	// 输出决策JSON
	Json::Value ret;
	int i = 0;
	if (opX >= 0 && turnID == 0) {//换手策略
        if(evaluationOfSwap(opX,opY)){
            Json::Value action;
            action["x"] = -1;
            action["y"] = -1;
            ret["response"] = action;
        }else{
            ret["response"] = getBestMovement(myColor);
        }
	} else {//正常对局策略
		ret["response"] = getBestMovement(myColor);
	}
	Json::FastWriter writer;
	cout << writer.write(ret) << endl;
	return 0;
}