#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define LAN "192.168.0.1"
#define DNS_MAX 16384
#define SO_ORIGINAL_DST 80
#define DNS_PENDING 32
#define DNS_UDP 1
#define DNS_TCP 2
struct job { struct sockaddr_in peer; size_t length; unsigned char packet[2048]; time_t since; };
struct metric { unsigned kind,ok,retries; unsigned long long sent,received; };
static int listeners[4]={-1,-1,-1,-1},events[2]={-1,-1};
static volatile sig_atomic_t stopping;
static volatile sig_atomic_t yield_idle;
static volatile unsigned *pending_work;
static void stop_signal(int sig) { (void)sig; stopping=1; }
static void wake_idle(int sig) { (void)sig; yield_idle=1; }
static void no_delay(int fd) {
    int enabled=1;
    (void)setsockopt(fd,IPPROTO_TCP,TCP_NODELAY,&enabled,sizeof(enabled));
}
static void timeouts(int fd,int seconds) {
    struct timeval t={seconds,0};
    (void)setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&t,sizeof(t));
    (void)setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&t,sizeof(t));
}
static int exact_io(int fd,void *buffer,size_t length,int writing) {
    unsigned char *p=buffer;
    while(length) {
        ssize_t n=writing?send(fd,p,length,MSG_NOSIGNAL):recv(fd,p,length,0);
        if(n<0&&errno==EINTR)continue;
        if(n<=0)return -1;
        p+=n;length-=(size_t)n;
    }
    return 0;
}
static int connect_to(const struct sockaddr_in *address) {
    int fd=socket(AF_INET,SOCK_STREAM,0);
    if(fd<0)return -1;
    if(fcntl(fd,F_SETFL,O_NONBLOCK)) {close(fd);return -1;}
    int result=connect(fd,(const struct sockaddr *)address,sizeof(*address));
    if(result&&errno!=EINPROGRESS) {close(fd);return -1;}
    if(result) {
        struct pollfd p={fd,POLLOUT,0};
        if(poll(&p,1,10000)<=0) {close(fd);return -1;}
        int error=0;socklen_t length=sizeof(error);
        if(getsockopt(fd,SOL_SOCKET,SO_ERROR,&error,&length)||error) {close(fd);return -1;}
    }
    if(fcntl(fd,F_SETFL,0)) {close(fd);return -1;}
    no_delay(fd);timeouts(fd,12);return fd;
}
static struct sockaddr_in ipv4(const char *text,unsigned port) {
    struct sockaddr_in a={0};a.sin_family=AF_INET;a.sin_port=htons((uint16_t)port);
    if(inet_pton(AF_INET,text,&a.sin_addr)!=1)a.sin_family=0;
    return a;
}
static int via_socks(const struct sockaddr_in *target) {
    struct sockaddr_in local=ipv4("127.0.0.1",1080);
    int fd=connect_to(&local);if(fd<0)return -1;
    unsigned char hello[3]={5,1,0},reply[4],request[10]={5,1,0,1};
    memcpy(request+4,&target->sin_addr,4);memcpy(request+8,&target->sin_port,2);
    if(exact_io(fd,hello,3,1)||exact_io(fd,reply,2,0)||reply[0]!=5||reply[1]!=0||
       exact_io(fd,request,10,1)||exact_io(fd,reply,4,0)||reply[0]!=5||reply[1]!=0) {close(fd);return -1;}
    unsigned char tail[258];size_t count=0;
    if(reply[3]==1)count=6;
    else if(reply[3]==4)count=18;
    else if(reply[3]==3) {if(exact_io(fd,tail,1,0)) {close(fd);return -1;} count=(size_t)tail[0]+2;}
    else {close(fd);return -1;}
    if(exact_io(fd,tail,count,0)) {close(fd);return -1;}
    return fd;
}
static void relay(int left,int right,struct metric *metric,volatile unsigned *idle_after) {
    struct pollfd p[2]={{left,POLLIN,0},{right,POLLIN,0}};
    int alive[2]={1,1};unsigned char buffer[8192];
    time_t last_report=0;
    time_t first_request=0;
    timeouts(left,12);timeouts(right,12);
    while(alive[0]||alive[1]) {
        p[0].fd=alive[0]?left:-1;p[1].fd=alive[1]?right:-1;
        int checking_idle=yield_idle;yield_idle=0;
        int timeout=20000;
        if(first_request && !metric->received){
            time_t elapsed=time(NULL)-first_request;
            int budget=*pending_work?10:25;
            if(elapsed>=budget)break;
            timeout=(int)(budget-elapsed)*1000;
        }
        int ready=poll(p,2,checking_idle?0:timeout);
        if(ready<0&&errno==EINTR)continue;
        if(!ready&&checking_idle){
            if(!*idle_after||(unsigned)time(NULL)-*idle_after<4)continue;
            (void)shutdown(left,SHUT_WR);
        }
        if(ready<=0)break;
        for(int i=0;i<2;i++)if(alive[i]&&(p[i].revents&(POLLIN|POLLHUP|POLLERR))) {
            ssize_t n=recv(p[i].fd,buffer,sizeof(buffer),0);
            if(n==0) {alive[i]=0;(void)shutdown(i?left:right,SHUT_WR);continue;}
            if(n<0||exact_io(i?left:right,buffer,(size_t)n,1))return;
            if(i==0){
                metric->sent+=(uint64_t)n;*idle_after=0;
                if(!first_request)first_request=time(NULL);
            }
            else {
                metric->received+=(uint64_t)n;
                time_t now=time(NULL);
                *idle_after=(unsigned)now;
                if(now-last_report>=3) {
                    int marker=open("/tmp/newman-usb/last-proxy-data",O_WRONLY|O_CREAT|O_TRUNC,0600);
                    if(marker>=0){close(marker);last_report=now;}
                }
            }
        }
    }
}
static int listener(const char *address,unsigned port,int udp) {
    int fd=socket(AF_INET,udp?SOCK_DGRAM:SOCK_STREAM,0),one=1;
    if(fd<0)return -1;
    (void)setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,&one,sizeof(one));
    struct sockaddr_in a=ipv4(address,port);
    if(bind(fd,(struct sockaddr *)&a,sizeof(a))||(!udp&&listen(fd,32))||fcntl(fd,F_SETFL,O_NONBLOCK)) {close(fd);return -1;}
    return fd;
}
static int question_end(const unsigned char *p,size_t length,unsigned *type) {
    if(length<17||p[4]!=0||p[5]!=1||(p[2]&0x80))return -1;
    size_t pos=12;
    while(pos<length&&p[pos]) {unsigned n=p[pos++];if(n>63||n>length-pos)return -1;pos+=n;}
    if(pos+5>length)return -1;
    pos++;*type=((unsigned)p[pos]<<8)|p[pos+1];return (int)(pos+4);
}
struct dns_pending {
    int used,sent,fd;uint16_t wire_id;
    struct job query;
};
static int dns_skip_name(const unsigned char *p,size_t n,size_t *pos) {
    for(unsigned steps=0;steps<128&&*pos<n;steps++) {
        unsigned size=p[(*pos)++];if(!size)return 0;
        if((size&0xc0)==0xc0) {if(*pos>=n)return -1;(*pos)++;return 0;}
        if(size>63||size>n-*pos)return -1;*pos+=size;
    }
    return -1;
}
static unsigned dns_u16(const unsigned char *p) {return ((unsigned)p[0]<<8)|p[1];}
static size_t dns_udp_limit(const struct job *q) {
    unsigned type;int end=question_end(q->packet,q->length,&type);if(end<0)return 512;
    if(q->packet[6]||q->packet[7]||q->packet[8]||q->packet[9])return 512;
    size_t pos=(size_t)end;
    for(unsigned i=0;i<dns_u16(q->packet+10);i++) {
        if(dns_skip_name(q->packet,q->length,&pos)||pos+10>q->length)break;
        unsigned kind=dns_u16(q->packet+pos),size=dns_u16(q->packet+pos+2),len=dns_u16(q->packet+pos+8);pos+=10;
        if(kind==41)return size<512?512:size>1232?1232:size;
        if(len>q->length-pos)break;pos+=len;
    }
    return 512;
}
static void dns_finish(struct dns_pending *pending,const unsigned char *answer,size_t length,unsigned failure) {
    unsigned char reply[DNS_MAX];
    if(answer) {memcpy(reply,answer,length);reply[0]=pending->query.packet[0];reply[1]=pending->query.packet[1];}
    else {
        unsigned type;int end=question_end(pending->query.packet,pending->query.length,&type);
        if(end<0)end=12;
        length=(size_t)end;memcpy(reply,pending->query.packet,length);
        reply[2]=(unsigned char)(0x80|(reply[2]&1));reply[3]=(unsigned char)(0x80|(failure&15));memset(reply+6,0,6);
    }
    if(pending->fd<0&&length>dns_udp_limit(&pending->query)) {
        unsigned type;int end=question_end(pending->query.packet,pending->query.length,&type);
        length=end<0?12:(size_t)end;memcpy(reply,pending->query.packet,length);
        reply[2]=(unsigned char)(0x82|(reply[2]&1));reply[3]=0x80;memset(reply+6,0,6);
    }
    int delivered=0;
    if(pending->fd>=0) {
        unsigned char header[2]={(unsigned char)(length>>8),(unsigned char)length};
        delivered=!exact_io(pending->fd,header,2,1)&&!exact_io(pending->fd,reply,length,1);
        close(pending->fd);
    } else delivered=sendto(listeners[2],reply,length,0,(struct sockaddr *)&pending->query.peer,sizeof(pending->query.peer))==(ssize_t)length;
    struct metric m={0};m.kind=pending->fd>=0?DNS_TCP:DNS_UDP;
    m.ok=delivered&&((reply[3]&15)==0||(reply[3]&15)==3);m.sent=pending->query.length;m.received=delivered?length:0;
    (void)send(events[1],&m,sizeof(m),MSG_DONTWAIT|MSG_NOSIGNAL);
    pending->used=0;
}
static void dns_disconnect(int *upstream,struct dns_pending *pending) {
    if(*upstream>=0)close(*upstream);*upstream=-1;
    for(int i=0;i<DNS_PENDING;i++)pending[i].sent=0;
}
static void dns_engine(void) {
    signal(SIGTERM,SIG_DFL);signal(SIGINT,SIG_DFL);signal(SIGHUP,SIG_IGN);
    for(int fd=3;fd<1024;fd++)if(fd!=listeners[2]&&fd!=listeners[3]&&fd!=events[1])close(fd);
    struct dns_pending pending[DNS_PENDING];memset(pending,0,sizeof(pending));
    int upstream=-1;uint16_t sequence=0;time_t retry=0;
    for(;;) {
        int waiting=0;
        for(int i=0;i<DNS_PENDING;i++)if(pending[i].used) {
            if(time(NULL)-pending[i].query.since>=12)dns_finish(&pending[i],NULL,0,2);
            else waiting++;
        }
        if(waiting&&upstream<0&&time(NULL)>=retry) {
            struct sockaddr_in target=ipv4("8.8.8.8",53);upstream=via_socks(&target);retry=time(NULL)+1;
            if(upstream>=0)timeouts(upstream,2);
        }
        if(upstream>=0)for(int i=0;i<DNS_PENDING;i++)if(pending[i].used&&!pending[i].sent) {
            struct dns_pending *q=&pending[i];unsigned char frame[2050];size_t n=q->query.length;
            frame[0]=(unsigned char)(n>>8);frame[1]=(unsigned char)n;memcpy(frame+2,q->query.packet,n);
            frame[2]=(unsigned char)(q->wire_id>>8);frame[3]=(unsigned char)q->wire_id;
            if(exact_io(upstream,frame,n+2,1)) {dns_disconnect(&upstream,pending);break;}
            q->sent=1;
        }
        struct pollfd p[3]={{listeners[2],POLLIN,0},{listeners[3],POLLIN,0},{upstream,POLLIN,0}};
        int ready=poll(p,3,100);if(ready<0&&errno!=EINTR)_exit(1);
        for(int kind=0;kind<2;kind++)if(p[kind].revents&POLLIN) {
            struct dns_pending query;memset(&query,0,sizeof(query));query.fd=-1;query.used=1;
            socklen_t peer_length=sizeof(query.query.peer);ssize_t n=-1;
            if(kind==0) n=recvfrom(listeners[2],query.query.packet,sizeof(query.query.packet),0,(struct sockaddr *)&query.query.peer,&peer_length);
            else {
                query.fd=accept(listeners[3],(struct sockaddr *)&query.query.peer,&peer_length);
                if(query.fd<0)continue;
                timeouts(query.fd,2);unsigned char header[2];
                if(!exact_io(query.fd,header,2,0)) {
                    size_t length=((size_t)header[0]<<8)|header[1];
                    if(length<=sizeof(query.query.packet)&&!exact_io(query.fd,query.query.packet,length,0))n=(ssize_t)length;
                }
            }
            unsigned qtype=0;
            if(n<17||(ntohl(query.query.peer.sin_addr.s_addr)&0xffffff00U)!=0xc0a80000U||question_end(query.query.packet,(size_t)n,&qtype)<0) {
                if(query.fd>=0)close(query.fd);continue;
            }
            query.query.length=(size_t)n;query.query.since=time(NULL);
            if(qtype==28) {dns_finish(&query,NULL,0,0);continue;}
            int slot=-1;for(int i=0;i<DNS_PENDING;i++)if(!pending[i].used) {slot=i;break;}
            if(slot<0) {dns_finish(&query,NULL,0,2);continue;}
            int collision;
            do {query.wire_id=++sequence;collision=0;for(int i=0;i<DNS_PENDING;i++)if(pending[i].used&&pending[i].wire_id==query.wire_id)collision=1;} while(collision);
            pending[slot]=query;
        }
        if(upstream>=0&&(p[2].revents&(POLLIN|POLLHUP|POLLERR))) {
            unsigned char header[2],answer[DNS_MAX];
            if(exact_io(upstream,header,2,0)) {dns_disconnect(&upstream,pending);continue;}
            size_t n=((size_t)header[0]<<8)|header[1];
            if(n<12||n>sizeof(answer)||exact_io(upstream,answer,n,0)||!(answer[2]&0x80)) {dns_disconnect(&upstream,pending);continue;}
            uint16_t id=(uint16_t)(((unsigned)answer[0]<<8)|answer[1]);
            for(int i=0;i<DNS_PENDING;i++)if(pending[i].used&&pending[i].wire_id==id) {dns_finish(&pending[i],answer,n,0);break;}
        }
    }
}

#define CLIENTS 48
/* Leave core capacity for DNS, health probes, and upstream sockets closing asynchronously. */
#define ACTIVE 16
static pid_t children[ACTIVE];
static volatile unsigned *idle_after;
static int clients[CLIENTS];
static time_t accepted[CLIENTS];
static time_t waiting_since[CLIENTS];
static void close_clients(void) {
    for(int i=0;i<CLIENTS;i++) if(clients[i]>=0)close(clients[i]);
}
static int dns_child(void) {
    pid_t pid=fork();
    if(pid==0){close_clients();dns_engine();_exit(0);}
    return pid;
}
static void client_worker(int slot,int active) {
    int fd=clients[slot];
    idle_after[active]=0;
    pid_t pid=fork();
    if(pid<0)return;
    if(pid==0){
        signal(SIGTERM,SIG_DFL);
        signal(SIGUSR1,wake_idle);
        clients[slot]=-1;close_clients();
        for(int i=0;i<4;i++)if(listeners[i]>=0)close(listeners[i]);
        close(events[0]);
        struct sockaddr_in target={0}; socklen_t size=sizeof(target);
        struct metric metric={0};metric.kind=3;
        if(!getsockopt(fd,SOL_IP,SO_ORIGINAL_DST,&target,&size)&&target.sin_family==AF_INET
           &&!(target.sin_addr.s_addr==inet_addr(LAN)&&target.sin_port==htons(12345))) {
            no_delay(fd);
            time_t started=time(NULL);
            int remote=via_socks(&target);
            /* No client payload has been consumed. A busy SOCKS worker can be retried safely. */
            while(remote<0&&metric.retries<2&&time(NULL)-started<2) {
                metric.retries++;(void)poll(NULL,0,200*(int)metric.retries);
                remote=via_socks(&target);
            }
            if(remote>=0){metric.ok=1;relay(fd,remote,&metric,&idle_after[active]);close(remote);}
        }
        send(events[1],&metric,sizeof(metric),MSG_DONTWAIT|MSG_NOSIGNAL);
        close(fd);_exit(0);
    }
    children[active]=pid;close(fd);clients[slot]=-1;
}
int main(void) {
    umask(077);signal(SIGPIPE,SIG_IGN);signal(SIGTERM,stop_signal);signal(SIGINT,stop_signal);
    for(int i=0;i<CLIENTS;i++)clients[i]=-1;
    idle_after=mmap(NULL,(ACTIVE+1)*sizeof(*idle_after),PROT_READ|PROT_WRITE,MAP_SHARED|MAP_ANONYMOUS,-1,0);
    if(idle_after==MAP_FAILED)return 2;
    pending_work=idle_after+ACTIVE;
    listeners[0]=listener(LAN,12345,0);
    listeners[2]=listener(LAN,1053,1);listeners[3]=listener(LAN,1053,0);
    if(listeners[0]<0||listeners[2]<0||listeners[3]<0||socketpair(AF_UNIX,SOCK_DGRAM,0,events))return 2;
    fcntl(events[0],F_SETFL,O_NONBLOCK);
    int dns=dns_child();
    if(dns<=0)return 3;
    unsigned long long sessions=0,responses=0,bytes=0,retries=0,failures=0;
    unsigned next_client=0,peak_queue=0,peak_ready=0,idle_wakes=0;
    time_t last_wake=0;
    while(!stopping) {
        pid_t done;
        while((done=waitpid(-1,NULL,WNOHANG))>0) {
            if(done==dns){dns=dns_child();if(dns<=0)stopping=1;}
            for(int k=0;k<ACTIVE;k++)if(children[k]==done)children[k]=0;
        }
        if(stopping)break;
        struct pollfd p[2+CLIENTS]={{listeners[0],POLLIN,0},{events[0],POLLIN,0}};
        int available=0,free_slot=-1;
        for(int k=0;k<ACTIVE;k++)if(!children[k])available++;
        unsigned queued=0;
        for(int i=0;i<CLIENTS;i++){
            p[2+i]=(struct pollfd){available||!waiting_since[i]?clients[i]:-1,POLLIN,0};
            if(clients[i]>=0)queued++;else if(free_slot<0)free_slot=i;
        }
        if(queued>peak_queue)peak_queue=queued;
        /* Let the kernel listen backlog apply backpressure when the user-space queue is full. */
        if(free_slot<0)p[0].fd=-1;
        int n=poll(p,2+CLIENTS,200);
        if(n<0&&errno!=EINTR)break;
        if(p[0].revents&POLLIN) {
            struct sockaddr_in peer={0};socklen_t size=sizeof(peer);
            int fd=accept(listeners[0],(struct sockaddr *)&peer,&size);
            if(fd>=0){
                if((ntohl(peer.sin_addr.s_addr)&0xffffff00U)!=0xc0a80000U)close(fd);
                else {clients[free_slot]=fd;accepted[free_slot]=time(NULL);waiting_since[free_slot]=0;}
            }
        }
        unsigned first=next_client,ready_count=0;
        time_t now=time(NULL),oldest=now;
        for(unsigned step=0;step<CLIENTS;step++) {
            unsigned i=(first+step)%CLIENTS;
            if(clients[i]<0)continue;
            if(now-accepted[i]>45){close(clients[i]);clients[i]=-1;continue;}
            if(waiting_since[i]&&!available){
                ready_count++;
                if(waiting_since[i]<oldest)oldest=waiting_since[i];
                continue;
            }
            if(!(p[2+i].revents&(POLLIN|POLLHUP|POLLERR)))continue;
            unsigned char byte;
            ssize_t got=recv(clients[i],&byte,1,MSG_PEEK|MSG_DONTWAIT);
            if(got==0||(got<0&&errno!=EINTR&&errno!=EAGAIN&&errno!=EWOULDBLOCK)){close(clients[i]);clients[i]=-1;continue;}
            if(got<0)continue;
            if(!waiting_since[i])waiting_since[i]=now;
            if(!available){ready_count++;if(waiting_since[i]<oldest)oldest=waiting_since[i];continue;}
            for(int k=0;k<ACTIVE;k++)if(!children[k]){
                client_worker((int)i,k);
                if(clients[i]<0){next_client=(i+1)%CLIENTS;available--;}
                break;
            }
        }
        if(ready_count>peak_ready)peak_ready=ready_count;
        *pending_work=ready_count;
        if(ready_count&&oldest<now&&!available&&now>last_wake){
            int candidate=-1;unsigned oldest_reply=(unsigned)now;
            for(int k=0;k<ACTIVE;k++){
                unsigned last=idle_after[k];
                if(children[k]>0 && last && last<oldest_reply && (unsigned)now-last>=4){
                    candidate=k;oldest_reply=last;
                }
            }
            if(candidate>=0){kill(children[candidate],SIGUSR1);last_wake=now;idle_wakes++;}
        }
        if(p[1].revents&POLLIN){
            struct metric m;
            while(recv(events[0],&m,sizeof(m),0)==sizeof(m)){
                if(m.kind==3){sessions+=m.ok;bytes+=m.received;retries+=m.retries;failures+=!m.ok;}else responses+=m.ok;
            }
            FILE *out=fopen("/tmp/newman-usb/traffic.new","w");
            if(out){
                fprintf(out,"tcp=%llu dns=%llu bytes=%llu queue_peak=%u ready_peak=%u idle_wakes=%u socks_retries=%llu socks_failed=%llu\n",
                        sessions,responses,bytes,peak_queue,peak_ready,idle_wakes,retries,failures);
                fclose(out);rename("/tmp/newman-usb/traffic.new","/tmp/newman-usb/traffic");
            }
        }
    }
    if(dns>0)kill(dns,SIGTERM);
    for(int k=0;k<ACTIVE;k++)if(children[k]>0)kill(children[k],SIGTERM);
    close_clients();
    while(waitpid(-1,NULL,0)>0){}
    return 0;
}
