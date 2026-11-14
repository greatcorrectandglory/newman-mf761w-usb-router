package main

import (
	"bytes"
	"testing"
)

func TestDNSQuestionAndTruncatedReply(t *testing.T) {
	query := []byte{0x12, 0x34, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 'a', 0, 0, 1, 0, 1}
	end, kind := dnsQuestion(query)
	if end != len(query) || kind != 1 {
		t.Fatalf("wrong DNS question: %d/%d", end, kind)
	}
	answer := synthetic(query, end, 0, true)
	if !bytes.Equal(answer[:2], query[:2]) || answer[2]&0x82 != 0x82 || answer[3]&0x80 == 0 {
		t.Fatal("invalid truncated reply")
	}
	for n := 0; n < len(query); n++ {
		if end, _ := dnsQuestion(query[:n]); end != 0 {
			t.Fatalf("accepted truncated query length %d", n)
		}
	}
}

func TestReassignedAddressUsesNewPeer(t *testing.T) {
	first := []byte{2, 0, 0, 0, 0, 1}
	second := []byte{2, 0, 0, 0, 0, 2}
	rememberPeer(101, first)
	rememberPeer(101, second)
	mac := clients[101]
	if !bytes.Equal(mac[:], second) {
		t.Fatal("stale client after lease reassignment")
	}
	if localClient([]byte{192, 168, 77, 1}) || !localClient([]byte{192, 168, 77, 101}) {
		t.Fatal("unexpected DHCP range")
	}
}
