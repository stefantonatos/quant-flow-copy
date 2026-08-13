//+------------------------------------------------------------------+
//| PowerOfThree.mq5                                                 |
//| MT5 port of strategies.py: PowerOfThree (key "po3").             |
//| ICT-style Accumulation/Manipulation/Distribution: mark the Asian |
//| session's high/low, wait for a London-session liquidity sweep of |
//| one side of that range followed by a close back inside (the      |
//| reversal signal), enter with a real SL/TP bracket, one trade/day.|
//|                                                                    |
//| IMPORTANT: session hours below are compared against bar time      |
//| shifted by InpServerUtcOffsetHours. MT5 bar times are your        |
//| BROKER'S SERVER TIME, not UTC -- often UTC+2 or +3. Check your    |
//| broker's actual server-time offset (Market Watch clock, or ask    |
//| their support) and set InpServerUtcOffsetHours accordingly, or    |
//| every session window below will be silently wrong.                |
//|                                                                    |
//| Python backtest (EURUSD, 15m, 1mo, default 2R) showed NO edge in  |
//| a quick test -- profit factor 0.77, and the optimizer's train/    |
//| test split showed classic overfitting on every params combo tried.|
//| This is exploratory, not a validated strategy. Treat accordingly. |
//+------------------------------------------------------------------+
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

input int    InpAsiaStartHour       = 0;    // Asian session start (0-23, UTC-equivalent)
input int    InpAsiaEndHour         = 8;    // Asian session end / manipulation window start
input int    InpManipEndHour        = 13;   // manipulation window end (London close-ish)
input int    InpSessionEndHour      = 21;   // force-flatten hour (NY close-ish)
input double InpRiskReward          = 2.0;  // take-profit distance, as a multiple of stop distance
input int    InpServerUtcOffsetHours= 0;    // YOUR BROKER'S server-time offset from UTC -- check this
input double InpLotSize             = 0.10;
input ulong  InpMagic               = 20260813;

int    g_lastDayCode = -1;
double g_asiaHigh = 0.0, g_asiaLow = 0.0;
bool   g_haveAsiaRange = false;
bool   g_sweptHigh = false, g_sweptLow = false;
bool   g_tradedToday = false;

int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagic);
   return(INIT_SUCCEEDED);
  }

bool IsNewBar()
  {
   static datetime lastTime = 0;
   datetime t = iTime(_Symbol, _Period, 0);
   if(t == lastTime) return(false);
   lastTime = t;
   return(true);
  }

int SessionHour(datetime t)
  {
   MqlDateTime dt;
   TimeToStruct(t, dt);
   int h = dt.hour - InpServerUtcOffsetHours;
   h = ((h % 24) + 24) % 24;
   return(h);
  }

void OnTick()
  {
   if(!IsNewBar()) return;

   double h[],l[],c[];
   ArraySetAsSeries(h,true); ArraySetAsSeries(l,true); ArraySetAsSeries(c,true);
   if(CopyHigh(_Symbol,_Period,1,1,h)!=1 || CopyLow(_Symbol,_Period,1,1,l)!=1 ||
      CopyClose(_Symbol,_Period,1,1,c)!=1) return;
   double high1=h[0], low1=l[0], close1=c[0];

   datetime t1 = iTime(_Symbol,_Period,1);
   MqlDateTime dt;
   TimeToStruct(t1, dt);
   int dayCode = dt.year*10000 + dt.mon*100 + dt.day;
   int sh = SessionHour(t1);

   if(dayCode != g_lastDayCode)
     {
      g_lastDayCode = dayCode;
      g_haveAsiaRange = false;
      g_sweptHigh = false;
      g_sweptLow = false;
      g_tradedToday = false;
     }

   // Force-flatten at session end, regardless of the SL/TP bracket.
   if(sh >= InpSessionEndHour && PositionSelect(_Symbol))
     {
      trade.PositionClose(_Symbol);
      return;
     }

   bool inAsia = (sh >= InpAsiaStartHour && sh < InpAsiaEndHour);
   if(inAsia)
     {
      if(!g_haveAsiaRange) { g_asiaHigh = high1; g_asiaLow = low1; g_haveAsiaRange = true; }
      else { g_asiaHigh = MathMax(g_asiaHigh, high1); g_asiaLow = MathMin(g_asiaLow, low1); }
      return;
     }

   bool inManip = (sh >= InpAsiaEndHour && sh < InpManipEndHour) && g_haveAsiaRange && !g_tradedToday;
   if(!inManip) return;

   if(high1 > g_asiaHigh) g_sweptHigh = true;
   if(low1  < g_asiaLow)  g_sweptLow  = true;

   // Already have an open position from an earlier bar this session -- let
   // the broker-side SL/TP manage it, nothing more to do here.
   if(PositionSelect(_Symbol)) return;

   if(g_sweptHigh && close1 < g_asiaHigh)
     {
      double sl = g_asiaHigh + (g_asiaHigh - g_asiaLow) * 0.05;   // small buffer past the swept high
      double dist = sl - close1;
      double tp = close1 - InpRiskReward * dist;
      trade.Sell(InpLotSize, _Symbol, 0.0, sl, tp);
      g_tradedToday = true;
     }
   else if(g_sweptLow && close1 > g_asiaLow)
     {
      double sl = g_asiaLow - (g_asiaHigh - g_asiaLow) * 0.05;
      double dist = close1 - sl;
      double tp = close1 + InpRiskReward * dist;
      trade.Buy(InpLotSize, _Symbol, 0.0, sl, tp);
      g_tradedToday = true;
     }
  }
//+------------------------------------------------------------------+
