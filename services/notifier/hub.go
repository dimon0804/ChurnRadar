package main

import (
	"sync"
)

// ResyncEvent is the SSE event name telling clients to refetch everything: the stream
// lost events (NATS outage) or the replica cannot replay from their Last-Event-ID.
const ResyncEvent = "resync"

// Event is one SSE message ready to be written to clients. Events without an ID are
// service messages: they are neither stored in the ring nor deduplicated.
type Event struct {
	ID   string
	Name string
	Data []byte
}

type client struct {
	ch     chan Event
	closed bool
}

// Hub fans events out to the clients connected to this replica and keeps a ring buffer
// of recent events so a reconnecting browser can catch up via Last-Event-ID.
type Hub struct {
	mu          sync.Mutex
	clients     map[*client]struct{}
	ring        []Event
	ringSize    int
	clientBuf   int
	onConnected func(n int)
	onDropped   func()
}

func NewHub(ringSize, clientBuf int) *Hub {
	return &Hub{
		clients:     make(map[*client]struct{}),
		ringSize:    ringSize,
		clientBuf:   clientBuf,
		onConnected: func(int) {},
		onDropped:   func() {},
	}
}

func resyncEvent(reason string) Event {
	return Event{Name: ResyncEvent, Data: []byte(`{"reason":"` + reason + `"}`)}
}

// Subscribe registers a client and returns the events it missed after lastID.
// When lastID is unknown to this replica (evicted from the ring, another replica,
// or lost in a NATS outage) nothing can be replayed, so the backlog is a single
// resync event and the client refetches its data.
func (h *Hub) Subscribe(lastID string) (*client, []Event) {
	h.mu.Lock()
	defer h.mu.Unlock()
	c := &client{ch: make(chan Event, h.clientBuf)}
	h.clients[c] = struct{}{}
	h.onConnected(len(h.clients))

	if lastID == "" {
		return c, nil
	}
	for i := len(h.ring) - 1; i >= 0; i-- {
		if h.ring[i].ID == lastID {
			return c, append([]Event(nil), h.ring[i+1:]...)
		}
	}
	return c, []Event{resyncEvent("unknown_last_event_id")}
}

func (h *Hub) Unsubscribe(c *client) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.removeLocked(c)
}

func (h *Hub) removeLocked(c *client) {
	if _, ok := h.clients[c]; !ok {
		return
	}
	delete(h.clients, c)
	if !c.closed {
		c.closed = true
		close(c.ch)
	}
	h.onConnected(len(h.clients))
}

// Publish stores the event and delivers it without blocking. A client whose buffer is
// full is disconnected: it will reconnect and resync instead of stalling everyone else.
// Returns false if an event with the same ID was already published.
func (h *Hub) Publish(ev Event) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	if ev.ID != "" {
		for i := len(h.ring) - 1; i >= 0; i-- {
			if h.ring[i].ID == ev.ID {
				return false
			}
		}
	}
	h.ring = append(h.ring, ev)
	if len(h.ring) > h.ringSize {
		h.ring = append([]Event(nil), h.ring[len(h.ring)-h.ringSize:]...)
	}
	for c := range h.clients {
		select {
		case c.ch <- ev:
		default:
			h.removeLocked(c)
			h.onDropped()
		}
	}
	return true
}

// Resync tells every client to refetch its data. The ring is cleared first: events
// published while the bus was unreachable are lost, so replaying from any older
// Last-Event-ID would silently skip them.
func (h *Hub) Resync(reason string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.ring = nil
	ev := resyncEvent(reason)
	for c := range h.clients {
		select {
		case c.ch <- ev:
		default:
			// a full buffer means the client is behind anyway; it reconnects with
			// an ID that is no longer in the ring and gets resync on Subscribe
			h.removeLocked(c)
			h.onDropped()
		}
	}
}

// CloseAll disconnects every client, used on graceful shutdown so browsers move to
// another replica right away.
func (h *Hub) CloseAll() {
	h.mu.Lock()
	defer h.mu.Unlock()
	for c := range h.clients {
		h.removeLocked(c)
	}
}

func (h *Hub) Clients() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.clients)
}
