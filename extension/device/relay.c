/* Carry this AP's Ethernet frames over the existing, USB-only ADB connection. */
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/filter.h>
#include <linux/if_packet.h>
#include <net/ethernet.h>
#include <net/if.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#define FRAME_MAX 2048
static int write_all(int fd, const void *data, size_t size) {
    const unsigned char *p = data;
    while (size) {
        ssize_t n = write(fd, p, size);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return -1;
        p += n; size -= (size_t)n;
    }
    return 0;
}
static int read_all(int fd, void *data, size_t size) {
    unsigned char *p = data;
    while (size) {
        ssize_t n = read(fd,p,size);
        if(n < 0 && errno == EINTR) continue;
        if(n <= 0) return -1;
        p += n; size -= (size_t)n;
    }
    return 0;
}
static int relay(int stream) {
    int raw = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
    if(raw < 0) {perror("AP packet socket"); return -1;}
    struct sockaddr_ll lan = { .sll_family=AF_PACKET, .sll_protocol=htons(ETH_P_ALL), .sll_ifindex=(int)if_nametoindex("br0") };
    if(!lan.sll_ifindex || bind(raw,(void *)&lan,sizeof lan)<0) { perror("AP bridge bind");close(raw); return -1; }
    struct ifreq req = {};
    snprintf(req.ifr_name,sizeof req.ifr_name,"br0");
    if(ioctl(raw,SIOCGIFHWADDR,&req)<0){perror("AP bridge identity");close(raw);return -1;}
    unsigned char hello[10]={'N','A','P','1'};
    memcpy(hello+4,req.ifr_hwaddr.sa_data,6);
    if(write_all(stream,hello,sizeof hello)<0){close(raw);return -1;}
    const struct timeval timeout={.tv_sec=5};
    setsockopt(stream,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof timeout);
    setsockopt(stream,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof timeout);
    struct pollfd fds[2]={{.fd=stream,.events=POLLIN},{.fd=raw,.events=POLLIN}};
    unsigned char frame[2+FRAME_MAX];
    for(;;) {
        int n = poll(fds,2,-1);
        if(n<0 && errno==EINTR)continue;
        if(n<0 || ((fds[0].revents|fds[1].revents) & (POLLHUP|POLLERR|POLLNVAL)))break;
        if(fds[0].revents&POLLIN) {
            if(read_all(stream,frame,2)<0)break;
            size_t len=((size_t)frame[0]<<8)|frame[1];
            if(len<14 || len>FRAME_MAX || read_all(stream,frame+2,len)<0)break;
            if(memcmp(frame+8,hello+4,6))break;
            uint16_t proto=((uint16_t)frame[14]<<8)|frame[15];
            if(proto!=ETH_P_IP && proto!=ETH_P_ARP)break;
            lan.sll_halen=6;lan.sll_protocol=htons(proto);memcpy(lan.sll_addr,frame+2,6);
            if(sendto(raw,frame+2,len,0,(void *)&lan,sizeof lan)<0)break;
        }
        if(fds[1].revents&POLLIN) {
            struct sockaddr_ll from; socklen_t flen=sizeof from;
            ssize_t len=recvfrom(raw,frame+2,FRAME_MAX,0,(void *)&from,&flen);
            if(len<0)break;
            if(len<14 || from.sll_pkttype==PACKET_OUTGOING)continue;
            uint16_t proto=((uint16_t)frame[14]<<8)|frame[15];
            if(proto!=ETH_P_IP && proto!=ETH_P_ARP)continue;
            frame[0]=(uint8_t)(len>>8);frame[1]=(uint8_t)len;
            if(write_all(stream,frame,len+2)<0)break;
        }
    }
    close(raw);return 0;
}
int main(void) {
    signal(SIGHUP,SIG_IGN);
    signal(SIGPIPE,SIG_IGN);
    int server=socket(AF_INET,SOCK_STREAM,0);if(server<0)return 1;
    int one=1;setsockopt(server,SOL_SOCKET,SO_REUSEADDR,&one,sizeof one);
    struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons(19874),.sin_addr={.s_addr=htonl(INADDR_LOOPBACK)}};
    if(bind(server,(void *)&addr,sizeof addr)<0 || listen(server,2)<0){perror("AP local listener");return 2;}
    for(;;){
        int c=accept(server,NULL,NULL);
        if(c<0 && errno==EINTR)continue;
        if(c<0)break;
        setsockopt(c,IPPROTO_TCP,TCP_NODELAY,&one,sizeof one);
        relay(c);close(c);
    }
    close(server);return 0;
}
