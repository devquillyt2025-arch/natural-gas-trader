-- The engine can now trade on 5-minute candles (CANDLE_MIN=5). Safe to run more than once.
alter table public.candles drop constraint if exists candles_tf_check;
alter table public.candles add constraint candles_tf_check check (tf in ('5m', '15m', '1d'));
