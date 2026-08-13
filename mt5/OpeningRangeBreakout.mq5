//+------------------------------------------------------------------+
//| OpeningRangeBreakout.mq5                                         |
//| MT5 port of strategies.py: OpeningRangeBreakout (key "orb").     |
//| Long/short breakout of the first N bars' high/low each UTC       |
//| session; flat and reset at every new session. Needs an intraday  |
//| timeframe to mean anything -- on daily bars each "session" is    |
//| one bar, so the range never finishes forming and it never trades.|
//|                                                                    |
//| Simplification vs. the Python port: that version has a           |
//| `flatten_eod` toggle, but since a new session always resets the  |
//| position to flat anyway (there's no in-between "after hours"     |
//| bar in a plain OHLC series), the toggle barely changes behavior. |
//| Dropped here rather than porting a near-no-op knob.              |
//+------------------------------------------------------------------+
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

input int    InpRangeBars = 6;     // opening-range length, in bars
input double InpLotSize   = 0.10;
input ulong  InpMagic     = 20260813;

int    g_lastDayCode = -1;
double g_orHigh = 0.0, g_orLow = 0.0;
int    g_barsInSession = 0;
int    g_holding = 0;   // 1=long, -1=short, 0=flat

int OnInit()
  {
   if(InpRangeBars < 1)
     {
      Print("OpeningRangeBreakout: InpRangeBars must be >= 1");
      return(INIT_FAILED);
     }
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

void ManagePosition(int holding)
  {
   bool haveLong=false, haveShort=false;
   if(PositionSelect(_Symbol))
     {
      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      haveLong  = (type==POSITION_TYPE_BUY);
      haveShort = (type==POSITION_TYPE_SELL);
     }
   if(holding==0)
     {
      if(haveLong || haveShort) trade.PositionClose(_Symbol);
      return;
     }
   if(holding==1 && !haveLong)
     {
      if(haveShort) trade.PositionClose(_Symbol);
      trade.Buy(InpLotSize, _Symbol);
     }
   else if(holding==-1 && !haveShort)
     {
      if(haveLong) trade.PositionClose(_Symbol);
      trade.Sell(InpLotSize, _Symbol);
     }
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

   if(dayCode != g_lastDayCode)
     {
      // New session: flatten (a new day always resets the range, so any
      // prior-day position is closed here rather than carried forward).
      if(g_holding != 0) { ManagePosition(0); g_holding = 0; }
      g_lastDayCode = dayCode;
      g_barsInSession = 0;
      g_orHigh = high1;
      g_orLow  = low1;
      return;   // this bar is part of the range forming, no breakout check yet
     }

   g_barsInSession++;
   if(g_barsInSession < InpRangeBars)
     {
      g_orHigh = MathMax(g_orHigh, high1);
      g_orLow  = MathMin(g_orLow, low1);
      return;   // still forming the opening range
     }

   if(close1 > g_orHigh)      g_holding = 1;
   else if(close1 < g_orLow)  g_holding = -1;
   // else: unchanged, matches the Python decide()'s sticky-state behavior

   ManagePosition(g_holding);
  }
//+------------------------------------------------------------------+
