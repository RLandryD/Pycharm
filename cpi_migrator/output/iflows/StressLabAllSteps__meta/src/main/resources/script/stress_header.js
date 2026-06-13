importClass(com.sap.gateway.ip.core.customdev.util.Message);

function processData(message) {
    message.setHeader('X-StressLab', 'js-step');
    return message;
}
