#import <Foundation/Foundation.h>
#import <Vision/Vision.h>
int main(void) {
    @autoreleasepool {
        NSData *data = [[NSFileHandle fileHandleWithStandardInput] readDataToEndOfFile];
        VNRecognizeTextRequest *request = [VNRecognizeTextRequest new];
        request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
        request.recognitionLanguages = @[@"ru-RU", @"en-US"];
        request.usesLanguageCorrection = NO;
        request.usesCPUOnly = YES;
        NSError *error = nil;
        VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithData:data options:@{}];
        if (![handler performRequests:@[request] error:&error]) return 2;
        NSMutableArray *items = [NSMutableArray array];
        for (VNRecognizedTextObservation *item in request.results) {
            VNRecognizedText *match = [item topCandidates:1].firstObject;
            if (!match) continue;
            CGRect box = item.boundingBox;
            [items addObject:@{@"text":match.string,@"x":@(CGRectGetMinX(box)),@"y":@(1-CGRectGetMidY(box)),@"h":@(CGRectGetHeight(box))}];
        }
        NSData *json = [NSJSONSerialization dataWithJSONObject:items options:0 error:&error];
        if (!json) return 3;
        [[NSFileHandle fileHandleWithStandardOutput] writeData:json];
        return 0;
    }
}
