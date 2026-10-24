package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// NATS subject -> SSE event name, as in contracts/README.md
var subjects = map[string]string{
	"churn.scores.updated":   "scores.updated",
	"churn.settings.changed": "settings.updated",
	"churn.source.changed":   "source.updated",
}

var (
	connectedClients = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "notifier_connected_clients", Help: "SSE clients connected to this replica",
	})
	eventsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "notifier_events_total", Help: "Events fanned out to SSE clients",
	}, []string{"type"})
	droppedClients = promauto.NewCounter(prometheus.CounterOpts{
		Name: "notifier_dropped_clients_total", Help: "Clients disconnected because they could not keep up",
	})
	natsConnected = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "notifier_nats_connected", Help: "1 when the NATS connection is up",
	})
	invalidEvents = promauto.NewCounter(prometheus.CounterOpts{
		Name: "notifier_invalid_events_total", Help: "Messages that could not be parsed as an event envelope",
	})
)

type envelope struct {
	EventID string          `json:"event_id"`
	Type    string          `json:"type"`
	Payload json.RawMessage `json:"payload"`
}

type config struct {
	port         string
	natsURL      string
	pingInterval time.Duration
	ringSize     int
	clientBuf    int
}

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func loadConfig() config {
	ping, err := time.ParseDuration(env("PING_INTERVAL", "15s"))
	if err != nil {
		ping = 15 * time.Second
	}
	ring, _ := strconv.Atoi(env("EVENT_BUFFER", "200"))
	buf, _ := strconv.Atoi(env("CLIENT_BUFFER", "64"))
	return config{
		port:         env("PORT", "8000"),
		natsURL:      env("NATS_URL", "nats://nats:4222"),
		pingInterval: ping,
		ringSize:     max(ring, 1),
		clientBuf:    max(buf, 1),
	}
}

// toEvent converts a NATS message into an SSE event.
func toEvent(subject string, data []byte) (Event, error) {
	name, ok := subjects[subject]
	if !ok {
		return Event{}, fmt.Errorf("unexpected subject %q", subject)
	}
	var env envelope
	if err := json.Unmarshal(data, &env); err != nil {
		return Event{}, err
	}
	if env.EventID == "" || len(env.Payload) == 0 {
		return Event{}, errors.New("envelope without event_id or payload")
	}
	return Event{ID: env.EventID, Name: name, Data: env.Payload}, nil
}

type server struct {
	hub      *Hub
	cfg      config
	ready    atomic.Bool
	draining atomic.Bool
	log      *slog.Logger
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"error": map[string]string{"code": code, "message": message}})
}

func writeEvent(w http.ResponseWriter, ev Event) error {
	_, err := fmt.Fprintf(w, "id: %s\nevent: %s\ndata: %s\n\n", ev.ID, ev.Name, ev.Data)
	return err
}

func (s *server) stream(w http.ResponseWriter, r *http.Request) {
	// identity headers are set by the gateway after ForwardAuth; direct calls are refused
	if r.Header.Get("X-User-Id") == "" {
		writeError(w, http.StatusUnauthorized, "unauthorized", "Нужно войти в систему")
		return
	}
	if s.draining.Load() {
		writeError(w, http.StatusServiceUnavailable, "draining", "Сервис перезапускается")
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "no_streaming", "Streaming unsupported")
		return
	}

	lastID := r.Header.Get("Last-Event-ID")
	if lastID == "" {
		lastID = r.URL.Query().Get("lastEventId")
	}
	c, backlog := s.hub.Subscribe(lastID)
	defer s.hub.Unsubscribe(c)

	h := w.Header()
	h.Set("Content-Type", "text/event-stream; charset=utf-8")
	h.Set("Cache-Control", "no-cache, no-transform")
	h.Set("Connection", "keep-alive")
	h.Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)

	rc := http.NewResponseController(w)
	_ = rc.SetWriteDeadline(time.Time{})

	if _, err := fmt.Fprint(w, "retry: 3000\n: connected\n\n"); err != nil {
		return
	}
	for _, ev := range backlog {
		if writeEvent(w, ev) != nil {
			return
		}
	}
	flusher.Flush()

	ticker := time.NewTicker(s.cfg.pingInterval)
	defer ticker.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case ev, open := <-c.ch:
			if !open {
				return
			}
			if writeEvent(w, ev) != nil {
				return
			}
			flusher.Flush()
		case <-ticker.C:
			if _, err := fmt.Fprint(w, ": ping\n\n"); err != nil {
				return
			}
			flusher.Flush()
		}
	}
}

func (s *server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/v1/stream", s.stream)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, _ *http.Request) {
		if !s.ready.Load() || s.draining.Load() {
			writeError(w, http.StatusServiceUnavailable, "not_ready", "NATS недоступен")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ready"}`))
	})
	mux.Handle("GET /metrics", promhttp.Handler())
	return mux
}

func (s *server) handleMsg(m *nats.Msg) {
	ev, err := toEvent(m.Subject, m.Data)
	if err != nil {
		invalidEvents.Inc()
		s.log.Warn("skip message", "subject", m.Subject, "error", err.Error())
		return
	}
	if s.hub.Publish(ev) {
		eventsTotal.WithLabelValues(ev.Name).Inc()
	}
}

func (s *server) connectNATS() (*nats.Conn, error) {
	nc, err := nats.Connect(s.cfg.natsURL,
		nats.Name("notifier"),
		nats.RetryOnFailedConnect(true),
		nats.MaxReconnects(-1),
		nats.ReconnectWait(2*time.Second),
		nats.ConnectHandler(func(*nats.Conn) { s.setReady(true) }),
		nats.ReconnectHandler(func(*nats.Conn) { s.setReady(true); s.log.Info("nats reconnected") }),
		nats.DisconnectErrHandler(func(_ *nats.Conn, err error) {
			s.setReady(false)
			s.log.Warn("nats disconnected", "error", fmt.Sprint(err))
		}),
	)
	if err != nil {
		return nil, err
	}
	if nc.IsConnected() {
		s.setReady(true)
	}
	// a plain core subscription per replica: every replica sees every event and serves
	// its own clients, so replicas scale out without coordination
	for subject := range subjects {
		if _, err := nc.Subscribe(subject, s.handleMsg); err != nil {
			nc.Close()
			return nil, err
		}
	}
	return nc, nil
}

func (s *server) setReady(v bool) {
	s.ready.Store(v)
	if v {
		natsConnected.Set(1)
	} else {
		natsConnected.Set(0)
	}
}

// healthcheck is used by the container HEALTHCHECK: the runtime image has no shell or curl.
func healthcheck(port string) int {
	client := http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get("http://127.0.0.1:" + port + "/healthz")
	if err != nil {
		return 1
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 1
	}
	return 0
}

func main() {
	cfg := loadConfig()
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(healthcheck(cfg.port))
	}
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil)).With("service", "notifier")
	hub := NewHub(cfg.ringSize, cfg.clientBuf)
	hub.onConnected = func(n int) { connectedClients.Set(float64(n)) }
	hub.onDropped = droppedClients.Inc

	s := &server{hub: hub, cfg: cfg, log: logger}
	nc, err := s.connectNATS()
	if err != nil {
		logger.Error("nats setup failed", "error", err.Error())
		os.Exit(1)
	}

	httpServer := &http.Server{
		Addr:              ":" + cfg.port,
		Handler:           s.routes(),
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       120 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	go func() {
		logger.Info("listening", "addr", httpServer.Addr, "nats", cfg.natsURL)
		if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("http server failed", "error", err.Error())
			os.Exit(1)
		}
	}()

	<-ctx.Done()
	logger.Info("shutting down", "clients", hub.Clients())
	s.draining.Store(true)
	hub.CloseAll()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = httpServer.Shutdown(shutdownCtx)
	_ = nc.Drain()
}
