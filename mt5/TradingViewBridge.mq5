//+------------------------------------------------------------------+
//|  TradingViewBridge.mq5                                           |
//|                                                                  |
//|  The execution half of the TradingView -> MT5 bridge. Watches a  |
//|  plain text queue file written by bridge/webhook_server.py and    |
//|  places the orders it finds there.                                |
//|                                                                  |
//|  WHY A TEXT FILE                                                  |
//|  A file is a boring interface, and boring is what you want        |
//|  between the internet and your account. You can open it in        |
//|  Notepad while it runs, see exactly what was asked for, and       |
//|  delete a line to cancel it. Sockets and DLL imports offer no     |
//|  advantage here and cost you that.                                |
//|                                                                  |
//|  INSTALL                                                          |
//|  1. MetaEditor -> open this file -> Compile (F7).                 |
//|  2. Point the webhook server's --queue at the SAME path as        |
//|     QueueFile below. MT5 sandboxes file access to                 |
//|     <terminal data folder>/MQL5/Files, so the server must write   |
//|     there. Find it via File -> Open Data Folder in MT5.           |
//|  3. Drag the EA onto any chart. The chart's symbol does not       |
//|     matter -- each command names its own symbol.                  |
//|  4. Enable AutoTrading (the button in the toolbar).               |
//|                                                                  |
//|  SAFETY                                                           |
//|  DryRun defaults TRUE: commands are read and printed to the       |
//|  Experts log, and nothing is sent to the broker. Leave it that    |
//|  way until the log shows exactly the trades you expected for      |
//|  several days. MaxLots is enforced here as well as on the         |
//|  server, on purpose -- two independent caps, because this is the  |
//|  half that spends money and it should not trust its input.        |
//+------------------------------------------------------------------+
#property copyright "quant-flow-copy"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

input string QueueFile      = "queue.txt";   // in MQL5/Files
input string ProcessedFile  = "queue_done.txt";
input bool   DryRun         = true;          // MUST be off for live orders
// MaxLots is a backstop against a malformed price producing an absurd
// position -- it is NOT the risk control. With risk-based sizing the real
// control is MaxRiskMoney, because that is the number denominated in the
// thing you actually care about losing.
//
// The default here used to be 0.10, which quietly rejected ordinary trades:
// a tight stop needs a LARGE lot size to risk a fixed amount. $100 over a
// 10-pip EURNZD stop is about 1.7 lots. A cap below that is not caution, it
// is a broken bridge that looks like caution.
input double MaxLots        = 5.0;           // backstop vs. malformed prices
input double MaxRiskMoney   = 200.0;         // THE risk control: max money per trade
input int    MaxSlippage    = 20;            // points
input int    PollSeconds    = 2;
input long   MagicNumber    = 20260817;
input bool   AllowClose     = true;

CTrade  trade;
int     g_processed = 0;   // how many lines of the queue we have consumed

//+------------------------------------------------------------------+
int OnInit()
  {
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(MaxSlippage);
   trade.SetTypeFillingBySymbol(_Symbol);

   g_processed = CountLines(ProcessedFile);
   PrintFormat("TradingViewBridge: %s. queue=%s already-processed=%d maxLots=%.2f",
               DryRun ? "DRY RUN (no orders will be sent)" : "LIVE",
               QueueFile, g_processed, MaxLots);
   if(!DryRun && !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      Print("WARNING: AutoTrading is disabled in the terminal. Nothing will execute.");

   EventSetTimer(PollSeconds < 1 ? 1 : PollSeconds);
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason) { EventKillTimer(); }

//+------------------------------------------------------------------+
//| Count lines in a file (0 if it does not exist).                   |
//+------------------------------------------------------------------+
int CountLines(string name)
  {
   int h = FileOpen(name, FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE)
      return 0;
   int n = 0;
   while(!FileIsEnding(h))
     {
      FileReadString(h);
      n++;
     }
   FileClose(h);
   return n;
  }

//+------------------------------------------------------------------+
//| Record that a command was handled, so a restart does not replay   |
//| the whole queue and re-open every position in it.                 |
//+------------------------------------------------------------------+
void MarkProcessed(string id, string verdict)
  {
   int h = FileOpen(ProcessedFile, FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_SHARE_READ);
   if(h == INVALID_HANDLE)
     {
      PrintFormat("Could not open %s to record progress", ProcessedFile);
      return;
     }
   FileSeek(h, 0, SEEK_END);
   FileWrite(h, TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + " " + id + " " + verdict);
   FileClose(h);
   g_processed++;
  }

//+------------------------------------------------------------------+
string GetField(string line, string key)
  {
   string parts[];
   int n = StringSplit(line, '|', parts);
   for(int i = 0; i < n; i++)
     {
      string kv[];
      if(StringSplit(parts[i], '=', kv) == 2 && kv[0] == key)
         return kv[1];
     }
   return "";
  }

//+------------------------------------------------------------------+
void OnTimer()
  {
   int h = FileOpen(QueueFile, FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE)
      return;                       // no queue yet; the server has not written one

   int index = 0;
   while(!FileIsEnding(h))
     {
      string line = FileReadString(h);
      index++;
      if(index <= g_processed)      // already handled on a previous poll
         continue;
      if(StringLen(line) < 5)
        {
         g_processed++;
         continue;
        }
      Execute(line);
     }
   FileClose(h);
  }

//+------------------------------------------------------------------+
//| Find the broker's name for a TradingView symbol.                  |
//|                                                                   |
//| These rarely match exactly. TradingView says EURNZD; brokers ship |
//| EURNZD.raw, EURNZDm, EURNZD-ECN and so on, and metals and indices |
//| are worse. An exact-match-only bridge silently refuses every       |
//| trade on such an account and looks broken.                        |
//|                                                                   |
//| Exact match first, then a prefix match across the broker's symbol  |
//| list. Whatever it resolves to is printed, because quietly trading  |
//| a symbol the user did not name is not acceptable -- they need to   |
//| see which instrument this actually is.                             |
//+------------------------------------------------------------------+
string ResolveSymbol(string want)
  {
   if(want == "")
      return "";
   if(SymbolSelect(want, true))
      return want;

   string upperWant = want;
   StringToUpper(upperWant);

   int total = SymbolsTotal(false);
   for(int i = 0; i < total; i++)
     {
      string s = SymbolName(i, false);
      string upperS = s;
      StringToUpper(upperS);
      if(StringFind(upperS, upperWant) == 0)     // broker name starts with it
        {
         if(SymbolSelect(s, true))
           {
            PrintFormat("Symbol %s not found; using broker symbol %s", want, s);
            return s;
           }
        }
     }
   return "";
  }

//+------------------------------------------------------------------+
//| Convert a money risk into a lot size.                             |
//|                                                                   |
//| This calculation lives HERE and not in the browser because it     |
//| needs the symbol's tick value and size and the account currency,  |
//| which only the terminal knows. A browser guessing at those would  |
//| silently size every trade wrong -- and wrong in a way that looks  |
//| completely normal until the loss arrives.                         |
//|                                                                   |
//| Returns 0 when it cannot be computed. 0 means REFUSE, never       |
//| "fall back to some default size".                                 |
//+------------------------------------------------------------------+
double LotsFromRisk(string symbol, double riskMoney, double entry, double sl)
  {
   if(riskMoney <= 0 || sl <= 0)
      return 0;

   double px = entry;
   if(px <= 0)                       // market order: size against current price
      px = SymbolInfoDouble(symbol, SYMBOL_BID);
   if(px <= 0)
      return 0;

   double dist = MathAbs(px - sl);
   if(dist <= 0)
      return 0;

   double tickSize  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tickSize <= 0 || tickValue <= 0)
     {
      PrintFormat("Cannot size %s: broker reports tickSize=%.10f tickValue=%.5f",
                  symbol, tickSize, tickValue);
      return 0;
     }

   double lossPerLot = (dist / tickSize) * tickValue;
   if(lossPerLot <= 0)
      return 0;

   return riskMoney / lossPerLot;
  }

//+------------------------------------------------------------------+
void Execute(string line)
  {
   string id     = GetField(line, "id");
   string action = GetField(line, "action");
   string symbol = GetField(line, "symbol");
   string side   = GetField(line, "side");
   string otype  = GetField(line, "type");
   double price  = StringToDouble(GetField(line, "price"));
   double sl     = StringToDouble(GetField(line, "sl"));
   double tp     = StringToDouble(GetField(line, "tp"));
   double lots   = StringToDouble(GetField(line, "lots"));
   double risk   = StringToDouble(GetField(line, "risk"));
   string cmt    = GetField(line, "comment");

   PrintFormat("CMD %s: %s %s %s %s lots=%.2f price=%s sl=%s tp=%s",
               id, action, side, otype, symbol, lots,
               DoubleToString(price, _Digits), DoubleToString(sl, _Digits),
               DoubleToString(tp, _Digits));

   string resolved = ResolveSymbol(symbol);
   if(resolved == "")
     {
      PrintFormat("REJECT %s: no symbol matching %s is available at this broker", id, symbol);
      MarkProcessed(id, "reject:unknown-symbol");
      return;
     }
   symbol = resolved;

   // Risk-based sizing: turn money into lots using this broker's actual
   // contract specs for this symbol.
   if(lots <= 0 && risk > 0)
     {
      if(risk > MaxRiskMoney)
        {
         PrintFormat("REJECT %s: risk %.2f exceeds EA cap %.2f", id, risk, MaxRiskMoney);
         MarkProcessed(id, "reject:risk-cap");
         return;
        }
      lots = LotsFromRisk(symbol, risk, price, sl);
      if(lots <= 0)
        {
         PrintFormat("REJECT %s: could not size %s from risk %.2f (missing contract specs or zero stop distance)",
                     id, symbol, risk);
         MarkProcessed(id, "reject:cannot-size");
         return;
        }
      PrintFormat("SIZING %s: risk %.2f over %.5f of stop distance -> %.4f lots (pre-rounding)",
                  id, risk, MathAbs(price - sl), lots);
     }

   if(lots <= 0)
     {
      PrintFormat("REJECT %s: no lots and no usable risk", id);
      MarkProcessed(id, "reject:no-size");
      return;
     }

   // Second, independent lot cap. The server has one too. This half of the
   // system spends money, so it does not trust its input.
   if(lots > MaxLots)
     {
      PrintFormat("REJECT %s: lots %.2f exceeds EA cap %.2f", id, lots, MaxLots);
      MarkProcessed(id, "reject:lots-cap");
      return;
     }

   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   // Round DOWN, always. Rounding up would risk more than was asked for.
   if(lotStep > 0)
      lots = MathFloor(lots / lotStep) * lotStep;
   if(lots < minLot)
     {
      // Refusing is the correct answer here, not trading the minimum. If the
      // requested risk needs less than one minimum lot, then the minimum lot
      // risks MORE than was asked -- silently, and by a factor the user never
      // agreed to. Say the number out loud instead.
      double wouldRisk = 0;
      if(risk > 0 && lots > 0)
         wouldRisk = risk * (minLot / lots);
      PrintFormat("REJECT %s: %.4f lots is below the symbol minimum %.4f. Trading the minimum would risk about %.2f, not %.2f. Widen the stop, raise the risk, or trade a smaller-contract symbol.",
                  id, lots, minLot, wouldRisk, risk);
      MarkProcessed(id, "reject:below-min-lot");
      return;
     }
   PrintFormat("SIZE %s: %.2f lots", id, lots);

   if(DryRun)
     {
      PrintFormat("DRY RUN %s: would have executed the above. Nothing sent.", id);
      MarkProcessed(id, "dryrun");
      return;
     }

   if(action == "close" || action == "close_all")
     {
      if(!AllowClose)
        {
         MarkProcessed(id, "reject:close-disabled");
         return;
        }
      bool ok = ClosePositions(symbol);
      MarkProcessed(id, ok ? "closed" : "close-failed");
      return;
     }

   bool isBuy = (side == "buy");
   bool ok = false;

   if(otype == "limit")
     {
      ok = isBuy ? trade.BuyLimit(lots, price, symbol, sl, tp, ORDER_TIME_GTC, 0, cmt)
                 : trade.SellLimit(lots, price, symbol, sl, tp, ORDER_TIME_GTC, 0, cmt);
     }
   else
     {
      ok = isBuy ? trade.Buy(lots, symbol, 0.0, sl, tp, cmt)
                 : trade.Sell(lots, symbol, 0.0, sl, tp, cmt);
     }

   if(ok)
      PrintFormat("OK %s: retcode=%d deal=%I64u", id, trade.ResultRetcode(), trade.ResultDeal());
   else
      PrintFormat("FAIL %s: retcode=%d (%s)", id, trade.ResultRetcode(), trade.ResultRetcodeDescription());

   MarkProcessed(id, ok ? "executed" : "failed");
  }

//+------------------------------------------------------------------+
//| Close only positions this EA opened -- never touch a position a   |
//| human placed by hand.                                             |
//+------------------------------------------------------------------+
bool ClosePositions(string symbol)
  {
   bool all = true;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;                       // not ours; leave it alone
      if(!trade.PositionClose(ticket))
        {
         PrintFormat("close failed for ticket %I64u: %d", ticket, trade.ResultRetcode());
         all = false;
        }
     }
   return all;
  }
//+------------------------------------------------------------------+
