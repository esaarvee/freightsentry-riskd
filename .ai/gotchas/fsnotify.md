# fsnotify Gotchas

## Watch the parent directory, NOT the file itself
Editors save atomically: write to a temp file → rename to target.
Watching the target file directly misses the rename event — the watcher is now watching a deleted inode.

```go
// WRONG — misses atomic editor saves
watcher.Add(filePath)

// CORRECT — watch the directory, filter events by filename
watcher.Add(filepath.Dir(filePath))
// In event loop: if event.Name == filePath { reload() }
```

File: `services/rules-engine/internal/rules/loader.go`

## Watcher registration is async — bare sleep in tests is unreliable
`Watch()` returns immediately; the goroutine that calls `watcher.Add(dir)` runs concurrently.
A test that writes the file immediately after `Watch()` returns may arrive before the watcher registers.

```go
// WRONG — racy
loader.Watch(path)
time.Sleep(20 * time.Millisecond)
os.WriteFile(path, newContent, 0644)

// CORRECT — pass a ready channel, signal after watcher.Add() succeeds
func (l *Loader) watch(path string, ready chan<- struct{}) {
    watcher.Add(filepath.Dir(path))
    if ready != nil { close(ready) }  // signal BEFORE event loop
    for { ... }
}
// In test:
ready := make(chan struct{})
go loader.watch(path, ready)
<-ready  // guaranteed watcher is registered
os.WriteFile(path, newContent, 0644)
```

File: `services/rules-engine/internal/rules/loader_test.go`
