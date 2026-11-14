// Windows userspace gateway for the dedicated MF761W USB Ethernet link.
package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/xjasonlyu/tun2socks/v2/core"
	"github.com/xjasonlyu/tun2socks/v2/core/adapter"
	"gvisor.dev/gvisor/pkg/buffer"
	"gvisor.dev/gvisor/pkg/tcpip"
	"gvisor.dev/gvisor/pkg/tcpip/link/channel"
	"gvisor.dev/gvisor/pkg/tcpip/network/ipv4"
	"gvisor.dev/gvisor/pkg/tcpip/stack"
)

var localIP = [4]byte{192, 168, 77, 1}
var localMAC = [6]byte{2, 78, 77, 0, 0, 1}
var be = binary.BigEndian
var rx, tx, tcpOK, tcpErr, dnsOK, dnsErr, quic atomic.Uint64
var socksAddress, stateFile string
var clients = map[byte][6]byte{}
var peersMu sync.RWMutex

// usbLink carries Ethernet frames from the AP bridge over USB ADB.
type gateway struct {
	usbLink net.Conn
	sendMu  sync.Mutex
	ep      *channel.Endpoint
}

func (g *gateway) frame(dst []byte, kind uint16, payload []byte) {
	size := max(60, 14+len(payload))
	out := make([]byte, size+2)
	be.PutUint16(out[:2], uint16(size))
	copy(out[2:8], dst)
	copy(out[8:14], localMAC[:])
	be.PutUint16(out[14:16], kind)
	copy(out[16:], payload)
	g.sendMu.Lock()
	g.usbLink.SetWriteDeadline(time.Now().Add(5 * time.Second))
	n, err := g.usbLink.Write(out)
	g.sendMu.Unlock()
	if err == nil && n == len(out) {
		tx.Add(1)
	} else {
		g.usbLink.Close()
	}
}
func checksum(b []byte) uint16 {
	var n uint32
	for len(b) >= 2 {
		n += uint32(be.Uint16(b))
		b = b[2:]
	}
	if len(b) > 0 {
		n += uint32(b[0]) << 8
	}
	for n>>16 != 0 {
		n = (n & 0xffff) + (n >> 16)
	}
	return ^uint16(n)
}
func packetIP(proto byte, dst []byte, payload []byte) []byte {
	o := make([]byte, 20+len(payload))
	o[0] = 0x45
	o[8] = 64
	o[9] = proto
	be.PutUint16(o[2:], uint16(len(o)))
	copy(o[12:], localIP[:])
	copy(o[16:], dst)
	be.PutUint16(o[10:], checksum(o[:20]))
	copy(o[20:], payload)
	return o
}
func (g *gateway) udp(mac, dst []byte, sport, dport uint16, msg []byte) {
	u := make([]byte, 8+len(msg))
	be.PutUint16(u, sport)
	be.PutUint16(u[2:], dport)
	be.PutUint16(u[4:], uint16(len(u)))
	copy(u[8:], msg)
	g.frame(mac, 0x800, packetIP(17, dst, u))
}
func localClient(ip []byte) bool {
	return len(ip) == 4 && ip[0] == 192 && ip[1] == 168 && ip[2] == 77 && ip[3] >= 20 && ip[3] < 251
}

// The AP assigns and renews leases. Update the link-layer peer when an address is reused.
func rememberPeer(last byte, source []byte) {
	var mac [6]byte
	copy(mac[:], source)
	peersMu.RLock()
	current, known := clients[last]
	peersMu.RUnlock()
	if known && current == mac {
		return
	}
	peersMu.Lock()
	clients[last] = mac
	peersMu.Unlock()
}
func dialProxy(ip string, port uint16) (net.Conn, error) {
	conn, e := net.DialTimeout("tcp", socksAddress, 5*time.Second)
	if e != nil {
		return nil, e
	}
	fail := func(e error) (net.Conn, error) { conn.Close(); return nil, e }
	conn.SetDeadline(time.Now().Add(12 * time.Second))
	if _, e = conn.Write([]byte{5, 1, 0}); e != nil {
		return fail(e)
	}
	var b [262]byte
	if _, e = io.ReadFull(conn, b[:2]); e != nil {
		return fail(e)
	}
	if b[0] != 5 || b[1] != 0 {
		return fail(errors.New("SOCKS negotiation failed"))
	}
	addr := net.ParseIP(ip).To4()
	if addr == nil {
		return fail(errors.New("IPv4 required"))
	}
	q := []byte{5, 1, 0, 1, 0, 0, 0, 0, byte(port >> 8), byte(port)}
	copy(q[4:8], addr)
	if _, e = conn.Write(q); e != nil {
		return fail(e)
	}
	if _, e = io.ReadFull(conn, b[:4]); e != nil {
		return fail(e)
	}
	if b[0] != 5 || b[1] != 0 {
		return fail(errors.New("SOCKS upstream unavailable"))
	}
	n := 6
	switch b[3] {
	case 1:
	case 4:
		n = 18
	case 3:
		if _, e = io.ReadFull(conn, b[:1]); e != nil {
			return fail(e)
		}
		n = int(b[0]) + 2
	default:
		return fail(errors.New("bad SOCKS address"))
	}
	if _, e = io.ReadFull(conn, b[:n]); e != nil {
		return fail(e)
	}
	conn.SetDeadline(time.Time{})
	return conn, nil
}

type handler struct{}

func (handler) HandleTCP(c adapter.TCPConn) {
	select {
	case tcpSlots <- struct{}{}:
	default:
		c.Close()
		return
	}
	go func() {
		defer func() { <-tcpSlots }()
		defer c.Close()
		id := c.ID()
		dest := id.LocalAddress.String()
		if id.LocalPort == 53 {
			dest = "8.8.8.8"
		}
		ip := net.ParseIP(dest)
		if ip == nil || ip.IsPrivate() || ip.IsLinkLocalUnicast() || ip.IsLoopback() || ip.IsMulticast() || ip.IsUnspecified() {
			return
		}
		r, e := dialProxy(dest, id.LocalPort)
		if e != nil {
			tcpErr.Add(1)
			return
		}
		defer r.Close()
		tcpOK.Add(1)
		done := make(chan struct{})
		go func() {
			io.Copy(r, c)
			if w, ok := r.(interface{ CloseWrite() error }); ok {
				w.CloseWrite()
			}
			close(done)
		}()
		io.Copy(c, r)
		c.Close()
		r.Close()
		<-done
	}()
}
func (handler) HandleUDP(c adapter.UDPConn) { c.Close() }
func dnsQuestion(q []byte) (int, uint16) {
	if len(q) < 17 || be.Uint16(q[4:6]) != 1 || q[2]&128 != 0 {
		return 0, 0
	}
	i := 12
	for i < len(q) && q[i] != 0 {
		n := int(q[i])
		if n > 63 || i+n+1 >= len(q) {
			return 0, 0
		}
		i += n + 1
	}
	if i+5 > len(q) {
		return 0, 0
	}
	return i + 5, be.Uint16(q[i+1 : i+3])
}
func synthetic(q []byte, end int, rcode byte, trunc bool) []byte {
	msg := append([]byte(nil), q[:end]...)
	msg[2] = 0x80 | (q[2] & 1)
	if trunc {
		msg[2] |= 2
	}
	msg[3] = 0x80 | rcode
	clear(msg[6:12])
	return msg
}

var dnsSlots = make(chan struct{}, 64)
var tcpSlots = make(chan struct{}, 512)

func (g *gateway) dns(mac, ip, query []byte, port uint16) {
	end, typ := dnsQuestion(query)
	if end == 0 {
		return
	}
	reply := func(msg []byte) { g.udp(mac, ip, 53, port, msg) }
	if typ == 28 {
		reply(synthetic(query, end, 0, false))
		return
	}
	select {
	case dnsSlots <- struct{}{}:
	default:
		reply(synthetic(query, end, 2, false))
		return
	}
	go func() {
		defer func() { <-dnsSlots }()
		fail := func() { dnsErr.Add(1); reply(synthetic(query, end, 2, false)) }
		r, e := dialProxy("8.8.8.8", 53)
		if e != nil {
			fail()
			return
		}
		defer r.Close()
		r.SetDeadline(time.Now().Add(8 * time.Second))
		q := make([]byte, len(query)+2)
		be.PutUint16(q, uint16(len(query)))
		copy(q[2:], query)
		if _, e = r.Write(q); e != nil {
			fail()
			return
		}
		if _, e = io.ReadFull(r, q[:2]); e != nil {
			fail()
			return
		}
		n := int(be.Uint16(q))
		if n < 12 || n > 16384 {
			fail()
			return
		}
		ans := make([]byte, n)
		if _, e = io.ReadFull(r, ans); e != nil || !bytes.Equal(query[:2], ans[:2]) || ans[2]&128 == 0 {
			fail()
			return
		}
		limit := 512
		if len(query) >= end+11 && query[end] == 0 && be.Uint16(query[end+1:]) == 41 {
			limit = min(1232, max(512, int(be.Uint16(query[end+3:]))))
		}
		if len(ans) > limit {
			ans = synthetic(query, end, 0, true)
		}
		dnsOK.Add(1)
		reply(ans)
	}()
}
func (g *gateway) receive(frame []byte) {
	if len(frame) < 42 {
		return
	}
	m := frame[6:12]
	if bytes.Equal(m, localMAC[:]) {
		return
	}
	switch be.Uint16(frame[12:14]) {
	case 0x0806:
		a := frame[14:42]
		if be.Uint16(a) != 1 || be.Uint16(a[2:]) != 0x800 || a[4] != 6 || a[5] != 4 || be.Uint16(a[6:]) != 1 || !bytes.Equal(a[24:28], localIP[:]) {
			return
		}
		reply := append([]byte(nil), a...)
		be.PutUint16(reply[6:8], 2)
		copy(reply[18:24], m)
		copy(reply[24:28], a[14:18])
		copy(reply[8:14], localMAC[:])
		copy(reply[14:18], localIP[:])
		g.frame(m, 0x806, reply)
	case 0x0800:
		raw := frame[14:]
		if raw[0]>>4 != 4 {
			return
		}
		hdr := int(raw[0]&15) * 4
		n := int(be.Uint16(raw[2:4]))
		if hdr < 20 || n < hdr || len(raw) < n {
			return
		}
		raw = raw[:n]
		data := raw[hdr:]
		src := raw[12:16]
		if raw[9] == 17 && len(data) >= 8 && be.Uint16(data[:2]) == 68 && be.Uint16(data[2:4]) == 67 {
			return
		}
		if !bytes.Equal(frame[:6], localMAC[:]) || !localClient(src) {
			return
		}
		rememberPeer(src[3], m)
		rx.Add(1)
		if raw[9] == 17 && len(data) >= 8 {
			port := be.Uint16(data[2:4])
			if port == 53 {
				g.dns(append([]byte(nil), m...), append([]byte(nil), src...), append([]byte(nil), data[8:]...), be.Uint16(data))
				return
			}
			if port == 443 {
				quic.Add(1)
			}
			ic := make([]byte, 8+min(len(raw), hdr+8))
			ic[0] = 3
			ic[1] = 3
			copy(ic[8:], raw)
			be.PutUint16(ic[2:], checksum(ic))
			g.frame(m, 0x800, packetIP(1, src, ic))
			return
		}
		if raw[9] != 6 && !(raw[9] == 1 && bytes.Equal(raw[16:20], localIP[:])) {
			return
		}
		pkt := stack.NewPacketBuffer(stack.PacketBufferOptions{Payload: buffer.MakeWithData(append([]byte(nil), raw...))})
		g.ep.InjectInbound(ipv4.ProtocolNumber, pkt)
		pkt.DecRef()
	}
}
func writeState() {
	for {
		peersMu.RLock()
		count := len(clients)
		peersMu.RUnlock()
		state := map[string]any{"pid": os.Getpid(), "timestamp": time.Now().Unix(), "clients": count, "ipv4_in": rx.Load(), "ethernet_out": tx.Load(), "tcp": tcpOK.Load(), "socks_errors": tcpErr.Load(), "dns": dnsOK.Load(), "dns_errors": dnsErr.Load(), "quic_reject": quic.Load()}
		data, _ := json.Marshal(state)
		if os.WriteFile(stateFile+".new", data, 0600) == nil {
			os.Rename(stateFile+".new", stateFile)
		}
		time.Sleep(time.Second)
	}
}
func main() {
	usbPort := flag.Int("usb-port", 19874, "dedicated ADB forward on loopback")
	port := flag.Int("socks", 19873, "local Xray SOCKS port")
	flag.StringVar(&stateFile, "state", "gateway-state.json", "aggregate counters")
	flag.Parse()
	socksAddress = net.JoinHostPort("127.0.0.1", strconv.Itoa(*port))
	link, e := net.DialTimeout("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(*usbPort)), 3*time.Second)
	if e != nil {
		fmt.Fprintln(os.Stderr, "USB AP relay unavailable")
		return
	}
	defer link.Close()
	link.SetReadDeadline(time.Now().Add(5 * time.Second))
	var hello [10]byte
	if _, e = io.ReadFull(link, hello[:]); e != nil || string(hello[:4]) != "NAP1" || hello[4]&1 != 0 {
		fmt.Fprintln(os.Stderr, "USB AP relay handshake unavailable")
		return
	}
	copy(localMAC[:], hello[4:])
	link.SetReadDeadline(time.Time{})
	ep := channel.New(1024, 1500, tcpip.LinkAddress(""))
	s, e := core.CreateStack(&core.Config{LinkEndpoint: ep, TransportHandler: handler{}})
	if e != nil {
		panic(e)
	}
	defer s.Close()
	g := &gateway{usbLink: link, ep: ep}
	go func() {
		for {
			p := ep.ReadContext(context.Background())
			if p == nil {
				return
			}
			v := p.ToView()
			raw := v.AsSlice()
			if len(raw) >= 20 && localClient(raw[16:20]) {
				peersMu.RLock()
				m, ok := clients[raw[19]]
				peersMu.RUnlock()
				if ok {
					g.frame(m[:], 0x800, raw)
				}
			}
			v.Release()
			p.DecRef()
		}
	}()
	go writeState()
	var data [2048]byte
	for {
		if _, e = io.ReadFull(link, data[:2]); e != nil {
			return
		}
		n := int(be.Uint16(data[:2]))
		if n < 14 || n > len(data) {
			return
		}
		if _, e = io.ReadFull(link, data[:n]); e != nil {
			return
		}
		g.receive(data[:n])
	}
}
