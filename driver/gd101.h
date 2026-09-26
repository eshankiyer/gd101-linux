#ifndef GD101_NATIVE_H
#define GD101_NATIVE_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Fixed-width J2534-style ABI. This Linux library is not a Windows DLL.
 * Partial implementation. All IDs/status codes are uint32_t.
 */
typedef struct {
    uint32_t ProtocolID, RxStatus, TxFlags, Timestamp, DataSize, ExtraDataIndex;
    unsigned char Data[4128];
} GD101_MESSAGE;
typedef struct {
    uint32_t NumOfBytes;
    unsigned char *BytePtr;
} GD101_BYTE_ARRAY;
typedef struct { uint32_t Parameter, Value; } GD101_CONFIG;
typedef struct { uint32_t NumOfParams; GD101_CONFIG *ConfigPtr; } GD101_CONFIG_LIST;
uint32_t PassThruOpen(const void *name, uint32_t *device_id);
uint32_t PassThruClose(uint32_t device_id);
uint32_t PassThruSetProgrammingVoltage(uint32_t device_id, uint32_t pin, uint32_t voltage);
uint32_t PassThruConnect(uint32_t device_id, uint32_t protocol,
                       uint32_t flags, uint32_t baud, uint32_t *channel_id);
uint32_t PassThruDisconnect(uint32_t channel_id);
uint32_t PassThruGetLastError(char *description); /* caller supplies 80 bytes */
uint32_t PassThruReadVersion(uint32_t device_id, char *firmware, char *driver,
                            char *api); /* caller supplies three 80-byte buffers */
uint32_t PassThruWriteMsgs(uint32_t channel_id, const GD101_MESSAGE *messages,
                         uint32_t *count, uint32_t timeout_ms);
uint32_t PassThruReadMsgs(uint32_t channel_id, GD101_MESSAGE *messages,
                        uint32_t *count, uint32_t timeout_ms);
uint32_t PassThruStartPeriodicMsg(uint32_t channel, const GD101_MESSAGE *message, uint32_t *handle, uint32_t interval);
uint32_t PassThruStopPeriodicMsg(uint32_t channel, uint32_t handle);
uint32_t PassThruStartMsgFilter(uint32_t channel_id, uint32_t type,
                              const GD101_MESSAGE *mask, const GD101_MESSAGE *pattern,
                              const GD101_MESSAGE *flow, uint32_t *filter_id);
uint32_t PassThruStopMsgFilter(uint32_t channel_id, uint32_t filter_id);
uint32_t PassThruIoctl(uint32_t id, uint32_t ioctl_id, const void *input, void *output);
#ifdef __cplusplus
}
#endif
#endif
