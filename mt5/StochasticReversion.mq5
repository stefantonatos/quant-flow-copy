//+------------------------------------------------------------------+
//| StochasticReversion.mq5                                          |
//| MT5 port of strategies.py: StochasticReversion (key "stoch").    |
//| Long while raw (unsmoothed) %K(14) is below oversold, flat once  |
//| %K rises above overbought. Long-only, no shorts -- matches the   |
//| Python engine exactly. Validated on 5yr EURUSD daily via         |
//| run.py optimize/walkforward/montecarlo before this port:         |
//| Sharpe 0.52, profit factor 1.73, profitable in 4/5 walk-forward  |
//| folds. See the chat/README for the full validation writeup --    |
//| this is a modest, real edge, not a guaranteed-profit system.     |
//+------------------------------------------------------------------+
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

input int    InpKPeriod   = 14;    // stochastic %K period
input double InpOversold  = 20.0;
input double InpOverbought= 80.0;
input double InpLotSize   = 0.10;
input ulong  InpMagic     = 20260813;

int  hStoch;
bool g_holding = false;

int OnInit()
  {
   // slowing=1 -> raw/fast %K (no extra smoothing), matching engine.py's
   // stochastic(): k = 100*(close-lowest)/(highest-lowest), no smoothing.
   hStoch = iStochastic(_Symbol, _Period, InpKPeriod, 3, 1, MODE_SMA, STO_LOWHIGH);
   if(hStoch == INVALID_HANDLE)
     {
      Print("StochasticReversion: failed to create the stochastic handle");
      return(INIT_FAILED);
     }
   trade.SetExpertMagicNumber(InpMagic);
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   IndicatorRelease(hStoch);
  }

bool IsNewBar()
  {
   static datetime lastTime = 0;
   datetime t = iTime(_Symbol, _Period, 0);
   if(t == lastTime) return(false);
   lastTime = t;
   return(true);
  }

void OnTick()
  {
   if(!IsNewBar()) return;

   double k[];
   ArraySetAsSeries(k, true);
   if(CopyBuffer(hStoch, MAIN_LINE, 1, 1, k) != 1) return;   // last closed bar's %K

   if(k[0] < InpOversold)      g_holding = true;
   else if(k[0] > InpOverbought) g_holding = false;
   // else: unchanged, matches the Python decide()'s sticky-state behavior

   bool haveLong = false;
   if(PositionSelect(_Symbol))
      haveLong = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);

   if(g_holding && !haveLong)
      trade.Buy(InpLotSize, _Symbol);
   else if(!g_holding && haveLong)
      trade.PositionClose(_Symbol);
  }
//+------------------------------------------------------------------+
