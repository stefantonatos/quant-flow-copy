//+------------------------------------------------------------------+
//| LorentzianClassification.mq5                                     |
//| Free MT5 port of jdehorty's "Machine Learning: Lorentzian        |
//| Classification v2.0" (public, MPL-2.0, TradingView Pine Script). |
//| Original: lorentzian_classification.pine in this repo's root.    |
//|                                                                    |
//| Ports the CORE ML entry/exit logic only -- KNN over Lorentzian   |
//| distance across RSI/WaveTrend/CCI/ADX, regime + volatility       |
//| filters, Nadaraya-Watson kernel trend filter, strict 4-bar hold  |
//| exits. Matches the Pine defaults: EMA/SMA filters off, ADX       |
//| filter off, dynamic exits off, kernel smoothing off. No SL/TP --  |
//| the original indicator doesn't define any either (that's a       |
//| separate bracket/SMC layer, out of scope here).                  |
//|                                                                    |
//| DELIBERATE DEVIATION FROM PINE (documented, not silent):         |
//| Pine's maxBarsBack window is anchored to the whole loaded         |
//| chart's known length (last_bar_index), which has no equivalent   |
//| in an EA that processes bars one at a time like a live feed.     |
//| This port uses a sliding "most recent N bars" training window    |
//| instead -- the sensible live/backtest equivalent, and bounds the |
//| per-bar KNN cost the same way maxBarsBack was meant to.          |
//|                                                                    |
//| Status: written but NOT yet confirmed to compile in MetaEditor   |
//| (no MQL5 compiler available in this environment). Paste into     |
//| MetaEditor, compile, and report any errors back for a fix --     |
//| same paste/error/fix loop used for the Pine port.                |
//+------------------------------------------------------------------+
#property copyright "Port of jdehorty's public MPL-2.0 indicator; this port free to use"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

// ================= Inputs (mirror the Pine defaults) =================
input int    InpNeighborsCount   = 8;      // K nearest neighbors
input int    InpMaxBarsBack      = 2000;   // sliding training-window size (see note above)
input double InpRegimeThreshold  = -0.1;   // regime filter threshold
input bool   InpUseVolatilityFilter = true;
input bool   InpUseRegimeFilter     = true;
input int    InpKernelH           = 8;     // kernel lookback window
input double InpKernelR           = 8.0;   // kernel relative weighting
input int    InpKernelLag         = 2;     // lag for the rate-of-change comparison
input int    InpFeatRsiA_Period   = 14;    // feature 1: RSI period
input int    InpFeatWtA_Period    = 10;    // feature 2: WaveTrend n1
input int    InpFeatWtB_Period    = 11;    // feature 2: WaveTrend n2
input int    InpFeatCciPeriod     = 20;    // feature 3: CCI period
input int    InpFeatAdxPeriod     = 20;    // feature 4: ADX period
input int    InpFeatRsiB_Period   = 9;     // feature 5: RSI period
input double InpLotSize           = 0.10;  // fixed lot size (no equity-based sizing -- add if you need it)
input ulong  InpMagic             = 20260813;

// ================= Indicator handles (built-in, incremental, fast) =================
int hRsiA, hRsiB, hCci, hAdx, hAtrShort, hAtrLong, hEmaWt1;

// ================= Persistent recursive state =================
// WaveTrend: ema1 comes from the built-in hEmaWt1 handle; ema2 (of |close-ema1|)
// and wt1 (ema of ci) are hand-rolled since MT5's iMA can't run on a derived series.
bool   g_wtSeeded = false;
double g_wtEma2 = 0.0, g_wtWt1 = 0.0;
double g_wt1Ring[4] = {0,0,0,0};   // last 4 wt1 values, for wt2 = SMA(wt1,4)
int    g_wt1RingCount = 0;

// Running historic min-max normalize (one lo/hi pair per feature)
double g_normLo[5], g_normHi[5];

// Regime filter (Kalman-like slope filter), all persistent per Pine's `var`
double g_rfValue1 = 0.0, g_rfValue2 = 0.0, g_rfKlmf = 0.0;
bool   g_rfSeeded = false;
double g_rfAvgSlope = 0.0;   // EMA(200) of abs(klmf change)
bool   g_rfAvgSeeded = false;

// Growable KNN training set: one row appended per confirmed bar, once warmed up.
double g_f1[], g_f2[], g_f3[], g_f4[], g_f5[];
int    g_labels[];

// Signal / entry history ring buffers, index0 = 1 bar ago ... index3 = 4 bars ago
// (exactly what Pine's signal[4] / startLongTrade[4] read).
int  g_sigHist[4]      = {0,0,0,0};
bool g_startLongHist[4]  = {false,false,false,false};
bool g_startShortHist[4] = {false,false,false,false};
int  g_prevSignal = 0;
int  g_barsHeld = 0;

int  g_warmupBars = 0;   // computed in OnInit from the feature periods

//+------------------------------------------------------------------+
int OnInit()
  {
   hRsiA = iRSI(_Symbol, _Period, InpFeatRsiA_Period, PRICE_CLOSE);
   hRsiB = iRSI(_Symbol, _Period, InpFeatRsiB_Period, PRICE_CLOSE);
   hCci  = iCCI(_Symbol, _Period, InpFeatCciPeriod, PRICE_TYPICAL);
   hAdx  = iADX(_Symbol, _Period, InpFeatAdxPeriod);
   hAtrShort = iATR(_Symbol, _Period, 1);
   hAtrLong  = iATR(_Symbol, _Period, 10);
   hEmaWt1   = iMA(_Symbol, _Period, InpFeatWtA_Period, 0, MODE_EMA, PRICE_CLOSE);

   if(hRsiA==INVALID_HANDLE || hRsiB==INVALID_HANDLE || hCci==INVALID_HANDLE ||
      hAdx==INVALID_HANDLE || hAtrShort==INVALID_HANDLE || hAtrLong==INVALID_HANDLE ||
      hEmaWt1==INVALID_HANDLE)
     {
      Print("LorentzianClassification: failed to create an indicator handle");
      return(INIT_FAILED);
     }

   trade.SetExpertMagicNumber(InpMagic);

   for(int i=0;i<5;i++){ g_normLo[i]=DBL_MAX; g_normHi[i]=-DBL_MAX; }

   // Warm up past the slowest feature (ADX needs ~2x its period) plus the
   // regime filter's EMA(200) slope average and the WaveTrend chain.
   g_warmupBars = MathMax(2*InpFeatAdxPeriod, MathMax(200, InpFeatWtA_Period+InpFeatWtB_Period+10)) + 5;

   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   IndicatorRelease(hRsiA); IndicatorRelease(hRsiB); IndicatorRelease(hCci);
   IndicatorRelease(hAdx);  IndicatorRelease(hAtrShort); IndicatorRelease(hAtrLong);
   IndicatorRelease(hEmaWt1);
  }

//+------------------------------------------------------------------+
bool IsNewBar()
  {
   static datetime lastTime = 0;
   datetime t = iTime(_Symbol, _Period, 0);
   if(t == lastTime) return(false);
   lastTime = t;
   return(true);
  }

//+------------------------------------------------------------------+
// Single buffer value at `shift` bars back (shift=1 == the last closed bar).
bool BufVal(int handle, int bufferIndex, int shift, double &out)
  {
   double buf[];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(handle, bufferIndex, shift, 1, buf) != 1) return(false);
   out = buf[0];
   return(true);
  }

//+------------------------------------------------------------------+
// Nadaraya-Watson rational-quadratic kernel estimate at `shift` bars back,
// over a lookback window (weights decay ~i^2, so this converges fast --
// window capped at 300 the same way the Python port caps it).
double KernelEstimate(int shift, int h, double r)
  {
   int window = 300;
   double closes[];
   ArraySetAsSeries(closes, true);
   int avail = Bars(_Symbol, _Period) - shift;
   int count = MathMin(window, avail);
   if(count < 2) return(EMPTY_VALUE);
   if(CopyClose(_Symbol, _Period, shift, count, closes) != count) return(EMPTY_VALUE);
   // closes[0] = bar at `shift`, closes[1] = shift+1, ... (series order, oldest last)
   double cw=0.0, tw=0.0;
   for(int i=0;i<count;i++)
     {
      double w = MathPow(1.0 + (double)(i*i) / ((double)h*h*2.0*r), -r);
      cw += closes[i]*w;
      tw += w;
     }
   return(tw>0 ? cw/tw : EMPTY_VALUE);
  }

//+------------------------------------------------------------------+
double NormalizeStep(int featIdx, double v)
  {
   if(v < g_normLo[featIdx]) g_normLo[featIdx] = v;
   if(v > g_normHi[featIdx]) g_normHi[featIdx] = v;
   double span = g_normHi[featIdx] - g_normLo[featIdx];
   return(span > 1e-10 ? (v - g_normLo[featIdx]) / span : 0.5);
  }

//+------------------------------------------------------------------+
// One WaveTrend step: ema1 supplied (built-in indicator), returns wt1-wt2
// already run through the running normalize -- i.e. feature 2 directly.
double WaveTrendFeatureStep(double close, double ema1)
  {
   double diff = MathAbs(close - ema1);
   double alphaN1 = 2.0 / (InpFeatWtA_Period + 1.0);
   double alphaN2 = 2.0 / (InpFeatWtB_Period + 1.0);
   if(!g_wtSeeded){ g_wtEma2 = diff; g_wtWt1 = 0.0; g_wtSeeded = true; }
   else            g_wtEma2 = alphaN1*diff + (1.0-alphaN1)*g_wtEma2;

   double ci = (g_wtEma2 > 1e-10) ? (close - ema1) / (0.015 * g_wtEma2) : 0.0;
   if(g_wt1RingCount==0) g_wtWt1 = ci;
   else                  g_wtWt1 = alphaN2*ci + (1.0-alphaN2)*g_wtWt1;

   // wt2 = SMA(wt1, 4) via a tiny ring buffer
   for(int k=3;k>0;k--) g_wt1Ring[k] = g_wt1Ring[k-1];
   g_wt1Ring[0] = g_wtWt1;
   g_wt1RingCount = MathMin(g_wt1RingCount+1, 4);
   double wt2 = 0.0;
   for(int k=0;k<g_wt1RingCount;k++) wt2 += g_wt1Ring[k];
   wt2 /= g_wt1RingCount;

   return NormalizeStep(1, g_wtWt1 - wt2);
  }

//+------------------------------------------------------------------+
// One regime-filter (Kalman-like slope) step. Returns true if the regime
// is "trending enough" per InpRegimeThreshold (always true if filter off).
bool RegimeFilterStep(double ohlc4, double high, double low)
  {
   if(!InpUseRegimeFilter) { g_rfSeeded=false; return(true); }  // keep state clean if toggled

   static double prevOhlc4 = 0.0;
   if(!g_rfSeeded){ prevOhlc4 = ohlc4; g_rfSeeded = true; }

   g_rfValue1 = 0.2*(ohlc4 - prevOhlc4) + 0.8*g_rfValue1;
   g_rfValue2 = 0.1*(high - low) + 0.8*g_rfValue2;
   prevOhlc4 = ohlc4;

   double omega = (g_rfValue2 != 0.0) ? MathAbs(g_rfValue1 / g_rfValue2) : 0.0;
   double alpha = (-omega*omega + MathSqrt(omega*omega*omega*omega + 16.0*omega*omega)) / 8.0;
   double prevKlmf = g_rfKlmf;
   g_rfKlmf = alpha*ohlc4 + (1.0-alpha)*prevKlmf;
   double absSlope = MathAbs(g_rfKlmf - prevKlmf);

   double emaAlpha = 2.0/201.0;   // EMA(200)
   if(!g_rfAvgSeeded){ g_rfAvgSlope = absSlope; g_rfAvgSeeded = true; }
   else                g_rfAvgSlope = emaAlpha*absSlope + (1.0-emaAlpha)*g_rfAvgSlope;

   if(g_rfAvgSlope == 0.0) return(true);
   return((absSlope - g_rfAvgSlope) / g_rfAvgSlope >= InpRegimeThreshold);
  }

//+------------------------------------------------------------------+
double LorentzianDistance(int j, int i)
  {
   return MathLog(1.0+MathAbs(g_f1[j]-g_f1[i])) + MathLog(1.0+MathAbs(g_f2[j]-g_f2[i])) +
          MathLog(1.0+MathAbs(g_f3[j]-g_f3[i])) + MathLog(1.0+MathAbs(g_f4[j]-g_f4[i])) +
          MathLog(1.0+MathAbs(g_f5[j]-g_f5[i]));
  }

//+------------------------------------------------------------------+
// KNN search for the just-appended row (index = last). Returns sum of the
// neighbor labels (Pine's `prediction`).
int PredictionSum()
  {
   int j = ArraySize(g_labels) - 1;
   int startIndex = MathMax(0, j - InpMaxBarsBack + 1);   // sliding window, see header note
   double lastDistance = -1.0;
   double distances[]; int preds[];
   ArrayResize(distances, 0); ArrayResize(preds, 0);

   for(int i=startIndex; i<=j; i++)
     {
      double d = LorentzianDistance(j, i);
      if(d >= lastDistance && i % 4 != 0)
        {
         lastDistance = d;
         int n = ArraySize(preds);
         ArrayResize(distances, n+1); distances[n] = d;
         ArrayResize(preds, n+1);     preds[n] = g_labels[i];
         if(ArraySize(preds) > InpNeighborsCount)
           {
            lastDistance = distances[(int)MathRound(InpNeighborsCount*3.0/4.0)];
            ArrayRemove(distances, 0, 1);
            ArrayRemove(preds, 0, 1);
           }
        }
     }
   int sum=0;
   for(int k=0;k<ArraySize(preds);k++) sum += preds[k];
   return sum;
  }

//+------------------------------------------------------------------+
void ManagePosition(bool startLong, bool startShort, bool endLong, bool endShort)
  {
   bool haveLong=false, haveShort=false;
   if(PositionSelect(_Symbol))
     {
      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      haveLong  = (type==POSITION_TYPE_BUY);
      haveShort = (type==POSITION_TYPE_SELL);
     }

   if(endLong && haveLong)   { trade.PositionClose(_Symbol); haveLong=false; }
   if(endShort && haveShort) { trade.PositionClose(_Symbol); haveShort=false; }

   if(startLong)
     {
      if(haveShort) trade.PositionClose(_Symbol);
      trade.Buy(InpLotSize, _Symbol);
     }
   else if(startShort)
     {
      if(haveLong) trade.PositionClose(_Symbol);
      trade.Sell(InpLotSize, _Symbol);
     }
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   if(!IsNewBar()) return;
   if(Bars(_Symbol, _Period) < g_warmupBars + 5) return;

   double open1,high1,low1,close1;
   {
      double o[],h[],l[],c[];
      ArraySetAsSeries(o,true); ArraySetAsSeries(h,true);
      ArraySetAsSeries(l,true); ArraySetAsSeries(c,true);
      if(CopyOpen(_Symbol,_Period,1,1,o)!=1 || CopyHigh(_Symbol,_Period,1,1,h)!=1 ||
         CopyLow(_Symbol,_Period,1,1,l)!=1  || CopyClose(_Symbol,_Period,1,1,c)!=1) return;
      open1=o[0]; high1=h[0]; low1=l[0]; close1=c[0];
   }
   double ohlc4 = (open1+high1+low1+close1)/4.0;

   // Regime + volatility filters run every bar regardless of feature warmup,
   // same as Pine's `var`-based filters.
   bool regimeOk = RegimeFilterStep(ohlc4, high1, low1);
   double atrS, atrL;
   bool volOk = true;
   if(InpUseVolatilityFilter)
     {
      if(!BufVal(hAtrShort,0,1,atrS) || !BufVal(hAtrLong,0,1,atrL)) return;
      volOk = (atrS > atrL);
     }
   bool filterAll = regimeOk && volOk;

   // ---- Features ----
   double rsiA, rsiB, cci, adxMain, ema1;
   if(!BufVal(hRsiA,0,1,rsiA) || !BufVal(hRsiB,0,1,rsiB) || !BufVal(hCci,0,1,cci) ||
      !BufVal(hAdx,MAIN_LINE,1,adxMain) || !BufVal(hEmaWt1,0,1,ema1))
      return;   // not warmed up yet

   double f1 = rsiA/100.0;
   double f2 = WaveTrendFeatureStep(close1, ema1);
   double f3 = NormalizeStep(2, cci);
   double f4 = adxMain/100.0;
   double f5 = rsiB/100.0;

   int predSum = 0;
   bool haveTrainingRow = false;
   if(Bars(_Symbol,_Period) >= g_warmupBars)
     {
      int n = ArraySize(g_labels);
      ArrayResize(g_f1,n+1); ArrayResize(g_f2,n+1); ArrayResize(g_f3,n+1);
      ArrayResize(g_f4,n+1); ArrayResize(g_f5,n+1); ArrayResize(g_labels,n+1);
      g_f1[n]=f1; g_f2[n]=f2; g_f3[n]=f3; g_f4[n]=f4; g_f5[n]=f5;

      // Label: sign of the trailing 4-bar move, same trailing definition as
      // Pine's `src[4] < src[0] ? short : ...` (see repo CLAUDE.md -- this
      // is NOT lookahead, both terms are already-known past prices).
      // shift=5 is exactly 4 bars before the bar at shift=1 (close1).
      double c4[];
      ArraySetAsSeries(c4,true);
      int lbl = 0;
      if(CopyClose(_Symbol,_Period,5,1,c4)==1)
        {
         double closeMinus4 = c4[0];
         if(closeMinus4 < close1) lbl = -1;
         else if(closeMinus4 > close1) lbl = 1;
        }
      g_labels[n] = lbl;

      haveTrainingRow = true;
      if(n >= 1) predSum = PredictionSum();
     }

   int newSignal = g_prevSignal;
   if(haveTrainingRow && filterAll)
     {
      if(predSum > 0) newSignal = 1;
      else if(predSum < 0) newSignal = -1;
     }

   bool different = (newSignal != g_sigHist[0]);
   g_barsHeld = different ? 0 : g_barsHeld + 1;
   bool held4 = (g_barsHeld == 4);
   bool heldLt4 = (g_barsHeld > 0 && g_barsHeld < 4);

   double yhatNow  = KernelEstimate(1, InpKernelH, InpKernelR);
   double yhatPrev = KernelEstimate(2, InpKernelH, InpKernelR);
   bool isBullishRate=false, isBearishRate=false;
   if(yhatNow!=EMPTY_VALUE && yhatPrev!=EMPTY_VALUE)
     {
      isBullishRate = (yhatPrev < yhatNow);
      isBearishRate = (yhatPrev > yhatNow);
     }

   bool isNewBuy  = (newSignal==1  && different);
   bool isNewSell = (newSignal==-1 && different);
   bool startLong  = isNewBuy  && isBullishRate;
   bool startShort = isNewSell && isBearishRate;

   bool lastWasBuy  = (g_sigHist[3]==1);
   bool lastWasSell = (g_sigHist[3]==-1);
   bool endLong  = ((held4 && lastWasBuy)  || (heldLt4 && isNewSell && lastWasBuy))  && g_startLongHist[3];
   bool endShort = ((held4 && lastWasSell) || (heldLt4 && isNewBuy  && lastWasSell)) && g_startShortHist[3];

   ManagePosition(startLong, startShort, endLong, endShort);

   // Shift ring buffers: today's values become "1 bar ago" next time.
   for(int k=3;k>0;k--)
     {
      g_sigHist[k]=g_sigHist[k-1];
      g_startLongHist[k]=g_startLongHist[k-1];
      g_startShortHist[k]=g_startShortHist[k-1];
     }
   g_sigHist[0]=newSignal;
   g_startLongHist[0]=startLong;
   g_startShortHist[0]=startShort;
   g_prevSignal = newSignal;
  }
//+------------------------------------------------------------------+
