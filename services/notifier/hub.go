package main

import (
	"sync"
)

// Event is one SSE message ready to be written to clients.
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

// Subscribe registers a client and returns the events it missed after lastID.
// When lastID is unknown to this replica nothing is replayed; the client is expected
// to refetch its data after reconnecting anyway.
func (h *Hub) Subscribe(lastID string) (*client, []Event) {
	h.mu.Lock()
	defer h.mu.Unlock()
	c := &client{ch: make(chan Event, h.clientBuf)}
	h.clients[c] = struct{}{}
	h.onConnected(len(h.clients))

	var backlog []Event
	if lastID != "" {
		for i := len(h.ring) - 1; i >= 0; i-- {
			if h.ring[i].ID == lastID {
				backlog = append(backlog, h.ring[i+1:]...)
				break
			}
		}
	}
	return c, backlog
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
