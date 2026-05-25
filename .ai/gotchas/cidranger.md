# cidranger Gotchas

## Dual rangers (separate) NOT a merged trie
cidranger overwrites same-network entries when two sets are merged before building the trie.
Merging L1 + L2 entries into one ranger breaks level precedence — L1 entries get silently overwritten.

```go
// WRONG — L1 entries overwritten by L2 if same CIDR exists in both
all := append(l1Entries, l2Entries...)
ranger, _ := cidranger.NewPCTrieRanger()
for _, e := range all { ranger.Insert(e) }

// CORRECT — separate rangers, check L1 first
BuildSnapshot(l1Entries, l2Entries) → snapshot{l1Ranger, l2Ranger}
// In Contains(): check l1Ranger first, fall back to l2Ranger
```

File: `services/rules-engine/internal/threatintel/store.go`

## cidranger.Insert overwrites by network key
Two entries with the same network CIDR but different data: Insert keeps only the last one.
This is by design — cidranger uses the CIDR network as the map key.
Never rely on ordering; always use separate ranger instances for logically distinct sets.
