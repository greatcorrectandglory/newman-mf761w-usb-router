#define _GNU_SOURCE
#include <sys/stat.h>
#include <sys/swap.h>
#include <sys/sysmacros.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>

/* Activate the factory-initialized compressed RAM device on a build without
   a swapon applet. Never initialize or format storage. */
int main(int argc,char **argv) {
    const char *path="/dev/zram0"; struct stat node;
    int disable=argc==2 && !strcmp(argv[1],"off");
    if(argc!=1 && !disable)return 2;
    FILE *list=fopen("/proc/swaps","r"); char line[256]; int active=0;
    if(!list)return 1;
    while(fgets(line,sizeof(line),list))if(!strncmp(line,"/dev/zram0 ",11)||!strncmp(line,"/dev/zram0\t",11))active=1;
    fclose(list);
    if(disable) {
        if(active && swapoff(path)){perror("swapoff zram");return 4;}
        return 0;
    }
    if(active){puts("zram active");return 0;}
    FILE *dev=fopen("/sys/block/zram0/dev","r");unsigned maj=0,min=0;
    if(!dev)return 1;
    int matched=fscanf(dev,"%u:%u",&maj,&min)==2;fclose(dev);
    if(!matched||stat(path,&node)||!S_ISBLK(node.st_mode)||node.st_rdev!=makedev(maj,min))return 2;
    int fd=open(path,O_RDONLY);char magic[10];
    if(fd<0||pread(fd,magic,10,sysconf(_SC_PAGESIZE)-10)!=10||memcmp(magic,"SWAPSPACE2",10))return 3;
    close(fd);
    if(swapon(path,0)){if(errno!=EBUSY){perror("swapon zram");return 4;}}
    puts("zram active");return 0;
}
