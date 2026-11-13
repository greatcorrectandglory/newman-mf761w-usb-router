#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
#include "bearssl.h"
#include "anchors.h"

static br_ssl_client_context client;
static br_x509_minimal_context verifier;
static unsigned char buffer[BR_SSL_BUFSIZE_BIDI];
static int socket_read(void *ctx, unsigned char *data, size_t length) {
    int n;
    do { n=(int)recv(*(int *)ctx,data,length,0); } while(n<0 && errno==EINTR);
    return n>0?n:-1;
}
static int socket_write(void *ctx, const unsigned char *data, size_t length) {
    int n;
    do { n=(int)send(*(int *)ctx,data,length,0); } while(n<0 && errno==EINTR);
    return n>0?n:-1;
}
static int read_exact(int fd, unsigned char *data, size_t n) {
    while(n>0) {
        int got=socket_read(&fd,data,n);
        if(got<0)return -1;
        data+=got; n-=(size_t)got;
    }
    return 0;
}
static int socks(int fd, const char *host) {
    unsigned char hello[]={5,1,0}, reply[260], request[260]={5,1,0,3};
    size_t size=strlen(host), left;
    request[4]=(unsigned char)size; memcpy(request+5,host,size);
    request[5+size]=1; request[6+size]=187;
    if(socket_write(&fd,hello,3)!=3 || read_exact(fd,reply,2) || memcmp(reply,"\x05\x00",2))return -1;
    if(socket_write(&fd,request,size+7)!=(int)(size+7) || read_exact(fd,reply,4) || reply[0]!=5 || reply[1]!=0)return -1;
    if(reply[3]==1)left=6;
    else if(reply[3]==4)left=18;
    else if(reply[3]==3 && !read_exact(fd,reply,1))left=reply[0]+2U;
    else return -1;
    return read_exact(fd,reply,left);
}
int main(int argc,char **argv) {
    const char *host,*path;
    int expected;
    if(argc!=2)return 2;
    if(!strcmp(argv[1],"google")){host="www.gstatic.com";path="/generate_204";expected=204;}
    else if(!strcmp(argv[1],"example")){host="example.com";path="/";expected=200;}
    else return 2;
    signal(SIGPIPE,SIG_IGN); alarm(12);
    int fd=socket(AF_INET,SOCK_STREAM,0);
    struct sockaddr_in addr={0};
    addr.sin_family=AF_INET;addr.sin_port=htons(1080);addr.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    if(fd<0||connect(fd,(struct sockaddr *)&addr,sizeof(addr)) || socks(fd,host))return 3;
    br_ssl_client_init_full(&client,&verifier,anchors,sizeof(anchors)/sizeof(anchors[0]));
    br_ssl_engine_set_versions(&client.eng,BR_TLS12,BR_TLS12);
    br_ssl_engine_set_buffer(&client.eng,buffer,sizeof(buffer),1);
    if(!br_ssl_client_reset(&client,host,0))return 4;
    br_sslio_context io;
    br_sslio_init(&io,&client.eng,socket_read,&fd,socket_write,&fd);
    char request[256],line[128];
    int len=snprintf(request,sizeof(request),"GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n",path,host);
    if(br_sslio_write_all(&io,request,(size_t)len)||br_sslio_flush(&io)) {
        printf("TLS error %d\n",br_ssl_engine_last_error(&client.eng)); return 5;
    }
    size_t used=0;
    while(used+1<sizeof(line)){
        int n=br_sslio_read(&io,line+used,sizeof(line)-1-used);
        if(n<=0)break;
        used+=(size_t)n;
        if(memchr(line,'\n',used))break;
    }
    line[used]='\0';int code=0;
    if(sscanf(line,"HTTP/1.%*d %d",&code)!=1||code!=expected){
        printf("HTTP %d, TLS error %d\n",code,br_ssl_engine_last_error(&client.eng));return 6;
    }
    close(fd);
    printf("HTTPS %d verified\n",code);
    return 0;
}
