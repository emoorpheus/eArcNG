// Синтетический фрагмент в форме, на которую рассчитаны замены trk501 (настоящий файл - в прошивке ТВ).
import QtQuick 2.4

Item {
    id: root
    property int shownVolume: LS.audioService.volume
    property bool exterior: LS.audioService.exterior
    property bool syncable: LS.audioService.volumeSyncable

    Image { source: false ? "arrows.png" : "speaker.png" }
    Text {
        id: number
        visible: !(false)
        text: root.shownVolume + ""
    }
    Rectangle {
        visible: !(false)
        width: 200 * root.shownVolume / 100
    }
}
