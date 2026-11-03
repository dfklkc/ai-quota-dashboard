#import <Cocoa/Cocoa.h>
#import <ScriptingBridge/ScriptingBridge.h>

@protocol QuotaChromeApplication
- (SBElementArray *)windows;
@end
@protocol QuotaChromeWindow
- (SBElementArray *)tabs;
- (NSInteger)activeTabIndex;
- (NSInteger)index;
@end
@protocol QuotaChromeTab
- (NSString *)URL;
- (id)executeJavascript:(NSString *)javascript;
@end
@interface EventErrors : NSObject <SBApplicationDelegate>
@property(nonatomic, strong) NSError *error;
@end
@implementation EventErrors
- (id)eventDidFail:(const AppleEvent *)event withError:(NSError *)error { self.error=error; return nil; }
@end
static void emit(NSDictionary *data) {
    NSData *json=[NSJSONSerialization dataWithJSONObject:data options:0 error:nil];
    if(json){fwrite(json.bytes,1,json.length,stdout);fputc('\n',stdout);fflush(stdout);}
}
// ScriptingBridge forwards this to AESendMessage: 60 ticks = 1 second.
static const long EventTimeoutTicks = 3 * 60;
static NSString *errorCode(NSError *error) {
    if(error.code==-1712)return @"timeout";
    if(error.code==-1743)return @"automation_denied";
    NSString *s=error.description;
    if([s containsString:@"JavaScript"]&&([s containsString:@"Apple"]||[s containsString:@"事件"]))return @"javascript_disabled";
    return @"browser_error";
}
int main(int argc,const char *argv[]) {
 @autoreleasepool {
    BOOL firstSession=argc==4&&strcmp(argv[3],"--first-session")==0;
    if(argc!=3&&!firstSession){emit(@{@"code":@"browser_error"});return 1;}
    NSString *start=@(argv[1]),*poll=@(argv[2]);
    BOOL running=NO,found=NO;
    NSDate *deadline=[NSDate dateWithTimeIntervalSinceNow:8];
    @try {
      for(NSRunningApplication *runningApp in [NSRunningApplication runningApplicationsWithBundleIdentifier:@"com.google.Chrome"]){
        if(runningApp.activationPolicy!=NSApplicationActivationPolicyRegular)continue;
        running=YES;
        SBApplication *base=[SBApplication applicationWithProcessIdentifier:runningApp.processIdentifier];
        EventErrors *errors=[EventErrors new];base.delegate=errors;base.timeout=EventTimeoutTicks;
        NSArray *windows=[[((id<QuotaChromeApplication>)base) windows] get];
        if(errors.error){emit(@{@"code":errorCode(errors.error), @"error_number":@(errors.error.code)});continue;}
        windows=[windows sortedArrayUsingComparator:^NSComparisonResult(id<QuotaChromeWindow> a,id<QuotaChromeWindow> b){return [@([a index]) compare:@([b index])];}];
        for(id<QuotaChromeWindow> win in windows){
          NSArray *tabs=[[win tabs] get];
          NSInteger active=[win activeTabIndex]-1;
          NSMutableArray *order=[NSMutableArray array];
          if(active>=0&&active<tabs.count)[order addObject:@(active)];
          for(NSInteger j=0;j<tabs.count;j++)if(j!=active)[order addObject:@(j)];
          for(NSNumber *number in order){
            if(deadline.timeIntervalSinceNow<=0){emit(@{@"code":@"timeout"});return 0;}
            id<QuotaChromeTab> tab=tabs[number.integerValue];
            errors.error=nil;
            NSString *url=[tab URL];
            if(![url isEqual:@"https://chatgpt.com"]&&![url hasPrefix:@"https://chatgpt.com/"])continue;
            found=YES;
            [tab executeJavascript:start];
            if(errors.error){emit(@{@"code":errorCode(errors.error), @"error_number":@(errors.error.code)});continue;}
            BOOL delivered=NO;
            for(int i=0;i<31&&deadline.timeIntervalSinceNow>0;i++){
              id raw=[tab executeJavascript:poll];
              if(errors.error){emit(@{@"code":errorCode(errors.error), @"error_number":@(errors.error.code)});delivered=YES;break;}
              NSDictionary *session=[raw isKindOfClass:NSString.class]?[NSJSONSerialization JSONObjectWithData:[raw dataUsingEncoding:NSUTF8StringEncoding] options:0 error:nil]:nil;
              if([session isKindOfClass:NSDictionary.class]&&![session[@"code"] isEqual:@"pending"]){
                NSMutableDictionary *out=[session mutableCopy];
                out[@"source_label"]=[NSString stringWithFormat:@"Chrome 窗口 %ld · 标签 %ld%@",(long)[win index],number.integerValue+1,number.integerValue==active?@"（活动）":@"（后台）"];
                emit(out);delivered=YES;
                // Follow mode needs only the prioritized account, not every background tab.
                if(firstSession&&[out[@"access_token"] isKindOfClass:NSString.class]&&[out[@"access_token"] length]>0)return 0;
                break;
              }
              [NSThread sleepForTimeInterval:0.1];
            }
            if(!delivered)emit(@{@"code":@"timeout"});
          }
        }
      }
      if(!found)emit(@{@"code":running?@"no_tab":@"chrome_closed"});
    }@catch(NSException *exception){emit(@{@"code":@"browser_error", @"exception":exception.name});}
 }
 return 0;
}
