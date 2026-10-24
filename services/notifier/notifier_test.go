package main

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func ev(id string) Event {
	return Event{ID: id, Name: "scores.updated", Data: []byte(`{"model_version":1}`)}
}

func TestPublishDeliversToAllClients(t *testing.T) {
	h := NewHub(10, 4)
	a, _ := h.Subscribe("")
	b, _ := h.Subscribe("")
	h.Publish(ev("1"))
	for _, c := range []*client{a, b} {
		select {
		case got := <-c.ch:
			if got.ID != "1" {
				t.Fatalf("got %q", got.ID)
			}
		default:
			t.Fatal("event not delivered")
		}
	}
}

func TestDuplicateEventIgnored(t *testing.T) {
	h := NewHub(10, 4)
	c, _ := h.Subscribe("")
	if !h.Publish(ev("1")) || h.Publish(ev("1")) {
		t.Fatal("duplicate must be rejected")
	}
	if len(c.ch) != 1 {
		t.Fatalf("expected 1 event, got %d", len(c.ch))
	}
}

func TestReplayAfterLastEventID(t *testing.T) {
	h := NewHub(3, 4)
	for i := 1; i <= 5; i++ {
		h.Publish(ev(fmt.Sprint(i)))
	}
	_, backlog := h.Subscribe("3")
	if len(backlog) != 2 || backlog[0].ID != "4" || backlog[1].ID != "5" {
		t.Fatalf("unexpected backlog %+v", backlog)
	}
	_, latest := h.Subscribe("5")
	if len(latest) != 0 {
		t.Fatalf("up-to-date client needs no backlog, got %+v", latest)
	}
	_, fresh := h.Subscribe("")
	if len(fresh) != 0 {
		t.Fatalf("new client needs no backlog, got %+v", fresh)
	}
}

func TestUnknownLastEventIDGetsResync(t *testing.T) {
	h := NewHub(3, 4)
	for i := 1; i <= 5; i++ {
		h.Publish(ev(fmt.Sprint(i)))
	}
	for _, id := range []string{"1", "foreign"} { // evicted from ring / never seen
		_, backlog := h.Subscribe(id)
		if len(backlog) != 1 || backlog[0].Name != ResyncEvent || backlog[0].ID != "" {
			t.Fatalf("id %q: expected a single resync, got %+v", id, backlog)
		}
	}
	empty := NewHub(3, 4) // replica restarted, ring is empty
	if _, backlog := empty.Subscribe("5"); len(backlog) != 1 || backlog[0].Name != ResyncEvent {
		t.Fatalf("empty ring: expected resync, got %+v", backlog)
	}
}

func TestResyncBroadcastsAndClearsRing(t *testing.T) {
	h := NewHub(10, 1)
	dropped := 0
	h.onDropped = func() { dropped++ }
	h.Publish(ev("1"))
	a, _ := h.Subscribe("")
	full, _ := h.Subscribe("")
	full.ch <- ev("stuck") // buffer of one is now full

	h.Resync("nats_reconnected")

	got := <-a.ch
	if got.Name != ResyncEvent || got.ID != "" || string(got.Data) != `{"reason":"nats_reconnected"}` {
		t.Fatalf("unexpected %+v", got)
	}
	if dropped != 1 || h.Clients() != 1 {
		t.Fatalf("client with full buffer must be dropped: dropped=%d clients=%d", dropped, h.Clients())
	}
	// events before the outage must not be replayed: there may be a gap after them
	if _, backlog := h.Subscribe("1"); len(backlog) != 1 || backlog[0].Name != ResyncEvent {
		t.Fatalf("pre-outage id must get resync, got %+v", backlog)
	}
	// dedup still works for new events
	if !h.Publish(ev("2")) || h.Publish(ev("2")) {
		t.Fatal("dedup after resync broken")
	}
}

func TestNatsUpResyncsClients(t *testing.T) {
	s, ts := newTestServer()
	defer ts.Close()
	s.ready.Store(false)
	c, _ := s.hub.Subscribe("")
	s.natsUp("nats_reconnected")
	if !s.ready.Load() {
		t.Fatal("must be ready after reconnect")
	}
	select {
	case got := <-c.ch:
		if got.Name != ResyncEvent {
			t.Fatalf("unexpected %+v", got)
		}
	default:
		t.Fatal("resync not delivered")
	}
}

func TestSlowClientIsDropped(t *testing.T) {
	h := NewHub(10, 1)
	dropped := 0
	h.onDropped = func() { dropped++ }
	slow, _ := h.Subscribe("")
	h.Publish(ev("1"))
	h.Publish(ev("2"))
	if dropped != 1 || h.Clients() != 0 {
		t.Fatalf("dropped=%d clients=%d", dropped, h.Clients())
	}
	<-slow.ch
	if _, open := <-slow.ch; open {
		t.Fatal("channel of dropped client must be closed")
	}
	h.Unsubscribe(slow) // must not panic on double close
}

func TestToEvent(t *testing.T) {
	got, err := toEvent("churn.settings.changed",
		[]byte(`{"event_id":"e1","type":"settings.changed","occurred_at":"x","schema_version":1,"payload":{"version":13,"updated_by":"admin"}}`))
	if err != nil {
		t.Fatal(err)
	}
	if got.Name != "settings.updated" || got.ID != "e1" || string(got.Data) != `{"version":13,"updated_by":"admin"}` {
		t.Fatalf("unexpected %+v", got)
	}
	if _, err := toEvent("churn.settings.changed", []byte(`{"payload":{}}`)); err == nil {
		t.Fatal("missing event_id must fail")
	}
	if _, err := toEvent("other", []byte(`{}`)); err == nil {
		t.Fatal("unknown subject must fail")
	}
}

func newTestServer() (*server, *httptest.Server) {
	s := &server{
		hub: NewHub(200, 16),
		cfg: config{pingInterval: 50 * time.Millisecond},
		log: slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
	s.ready.Store(true)
	return s, httptest.NewServer(s.routes())
}

func TestStreamRequiresGatewayIdentity(t *testing.T) {
	_, ts := newTestServer()
	defer ts.Close()
	resp, err := http.Get(ts.URL + "/api/v1/stream")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("status %d", resp.StatusCode)
	}
}

func TestStreamDeliversEventsAndPings(t *testing.T) {
	s, ts := newTestServer()
	defer ts.Close()
	s.hub.Publish(ev("old"))

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, ts.URL+"/api/v1/stream", nil)
	req.Header.Set("X-User-Id", "u1")
	req.Header.Set("Last-Event-ID", "missing")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if ct := resp.Header.Get("Content-Type"); !strings.HasPrefix(ct, "text/event-stream") {
		t.Fatalf("content type %q", ct)
	}

	go func() {
		for s.hub.Clients() == 0 {
			time.Sleep(5 * time.Millisecond)
		}
		s.hub.Publish(ev("new"))
	}()

	var sawResync, sawEvent, sawPing bool
	sc := bufio.NewScanner(resp.Body)
	for sc.Scan() && !(sawResync && sawEvent && sawPing) {
		line := sc.Text()
		if line == "id: old" {
			t.Fatal("unknown Last-Event-ID must not replay")
		}
		if line == "event: resync" {
			if sawEvent {
				t.Fatal("resync must come before live events")
			}
			sawResync = true
		}
		if line == "id: new" {
			sawEvent = true
		}
		if line == ": ping" {
			sawPing = true
		}
	}
	if !sawResync || !sawEvent || !sawPing {
		t.Fatalf("resync=%v event=%v ping=%v", sawResync, sawEvent, sawPing)
	}
}

func TestReadyz(t *testing.T) {
	s, ts := newTestServer()
	defer ts.Close()
	s.ready.Store(false)
	resp, _ := http.Get(ts.URL + "/readyz")
	resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("status %d", resp.StatusCode)
	}
}
