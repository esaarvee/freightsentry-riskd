# miniredis/v2 Gotchas

## mr.Get() returns (string, error) — always two-value
```go
// WRONG — does not compile
val := mr.Get("key")

// CORRECT
val, err := mr.Get("key")
```
Unlike redis client `.Get()` which returns `*redis.StringCmd`, the miniredis server method returns raw values.

## mr.TTL() vs client.TTL()
Assert TTL via `mr.TTL(key)` (miniredis server method), not via the Redis client's `.TTL()`.
Client TTL adds a round-trip and network noise; mr.TTL() is synchronous and exact.

## blockDuration=0 blocks forever
`ReadGroup` with `blockDuration=0` maps to `BLOCK 0` — blocks indefinitely waiting for messages.
In tests where no messages are expected: use `20*time.Millisecond`, never `0`.
In tests where messages are expected: enqueue the message before calling ReadGroup, then use `0` or short timeout.

## RunT vs Run
Prefer `miniredis.RunT(t)` over `miniredis.Run()` — RunT registers automatic cleanup via `t.Cleanup`.
With `Run()` you must call `mr.Close()` manually; forgetting it leaks the server across tests.
